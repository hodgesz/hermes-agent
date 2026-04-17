"""Allowlist-based network egress policy for URL-capable tools.

This is the fail-*closed* companion to :mod:`tools.website_policy` (which is a
fail-open blocklist).  When enabled, only destinations matching an explicit
allowlist rule are permitted; everything else is blocked.

Rules are loaded from ``~/.hermes/config.yaml`` under
``security.network_allowlist``.  Two optional auto-allow toggles avoid
double-configuration:

- ``auto_allow_providers``: permit hosts of configured inference endpoints
  (PROVIDER_REGISTRY entries + user ``providers:`` custom endpoints).
- ``auto_allow_mcp``: permit hosts of HTTP-transport MCP servers declared in
  the ``mcp_servers:`` config.

The parsed policy is cached with a short TTL so repeated egress checks are
cheap (web_crawl with 50 pages doesn't re-parse config 51 times).

Public API:
    ``check_network_egress(url)`` -> ``None`` if allowed, dict with block
    metadata (``host``, ``port``, ``message``) if denied.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

_DEFAULT_NETWORK_ALLOWLIST = {
    "enabled": False,
    "rules": [],
    "shared_files": [],
    "auto_allow_providers": True,
    "auto_allow_mcp": True,
}

# Cache: parsed policy + timestamp.
_CACHE_TTL_SECONDS = 30.0
_cache_lock = threading.Lock()
_cached_policy: Optional[Dict[str, Any]] = None
_cached_policy_path: Optional[str] = None
_cached_policy_time: float = 0.0


def _get_default_config_path() -> Path:
    return get_hermes_home() / "config.yaml"


class NetworkPolicyError(Exception):
    """Raised when the network allowlist config is malformed."""


def _normalize_host(host: str) -> str:
    host = (host or "").strip().lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_domain_pattern(pattern: Any) -> Optional[str]:
    if not isinstance(pattern, str):
        return None
    value = pattern.strip().lower()
    if not value or value.startswith("#"):
        return None
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.netloc or parsed.path
        # Strip user:pass@ prefix and :port suffix — ports travel as a
        # separate field on the rule.
        if "@" in value:
            value = value.rsplit("@", 1)[1]
        if value.startswith("[") and "]" in value:
            value = value.split("]", 1)[0] + "]"
        else:
            value = value.rsplit(":", 1)[0] if value.count(":") == 1 else value
    value = value.split("/", 1)[0].strip().rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    return value or None


def _coerce_port_list(value: Any) -> List[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result: List[int] = []
        for item in value:
            try:
                port = int(item)
            except (TypeError, ValueError):
                continue
            if 0 < port < 65536:
                result.append(port)
        return result
    try:
        port = int(value)
    except (TypeError, ValueError):
        return []
    return [port] if 0 < port < 65536 else []


def _parse_rule_entry(raw: Any, source: str) -> Optional[Dict[str, Any]]:
    """Parse a single allowlist rule into a normalized dict.

    Accepts either a string (``"example.com"``) or a mapping with ``domain``,
    ``ports``, and optional ``description``.
    """
    if isinstance(raw, str):
        pattern = _normalize_domain_pattern(raw)
        if not pattern:
            return None
        return {"pattern": pattern, "ports": [], "source": source, "description": ""}

    if not isinstance(raw, dict):
        return None

    pattern = _normalize_domain_pattern(raw.get("domain") or raw.get("host") or raw.get("pattern"))
    if not pattern:
        return None

    ports = _coerce_port_list(raw.get("ports") or raw.get("port"))
    description = raw.get("description") or ""
    if not isinstance(description, str):
        description = ""

    return {
        "pattern": pattern,
        "ports": ports,
        "source": source,
        "description": description,
    }


def _iter_shared_file_rules(path: Path) -> List[Dict[str, Any]]:
    """Load allowlist rules from a shared text file.

    Each non-comment line is a domain pattern (same syntax as
    ``security.network_allowlist.rules`` string form).  Missing or unreadable
    files log a warning and return an empty list rather than raising — a bad
    path should not block all network access.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Shared allowlist file not found (skipping): %s", path)
        return []
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read shared allowlist file %s (skipping): %s", path, exc)
        return []

    source = str(path)
    rules: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entry = _parse_rule_entry(stripped, source)
        if entry is not None:
            rules.append(entry)
    return rules


def _load_policy_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = config_path or _get_default_config_path()
    if not config_path.exists():
        return dict(_DEFAULT_NETWORK_ALLOWLIST)

    try:
        import yaml
    except ImportError:
        logger.debug("PyYAML not installed — network allowlist disabled")
        return dict(_DEFAULT_NETWORK_ALLOWLIST)

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise NetworkPolicyError(f"Invalid config YAML at {config_path}: {exc}") from exc
    except OSError as exc:
        raise NetworkPolicyError(f"Failed to read config file {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise NetworkPolicyError("config root must be a mapping")

    security = config.get("security", {})
    if security is None:
        security = {}
    if not isinstance(security, dict):
        raise NetworkPolicyError("security must be a mapping")

    network_allowlist = security.get("network_allowlist", {})
    if network_allowlist is None:
        network_allowlist = {}
    if not isinstance(network_allowlist, dict):
        raise NetworkPolicyError("security.network_allowlist must be a mapping")

    policy = dict(_DEFAULT_NETWORK_ALLOWLIST)
    policy.update(network_allowlist)
    # Stash the raw config so provider/MCP auto-allow can look up endpoints.
    policy["__config_root__"] = config
    return policy


def _auto_allow_provider_hosts(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return rules derived from configured inference providers.

    Pulls from PROVIDER_REGISTRY (built-in catalog) plus user-defined custom
    providers.  If an env var override exists for a provider's base URL we
    honor that; otherwise we use the registry default.
    """
    import os

    rules: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def _add_url(url: str, source: str) -> None:
        if not url or "://" not in url:
            return
        parsed = urlparse(url)
        host = _normalize_host(parsed.hostname or "")
        if not host or host in seen:
            return
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
        ports = [port] if port else []
        seen.add(host)
        rules.append({
            "pattern": host,
            "ports": ports,
            "source": source,
            "description": f"auto-allowed provider ({source})",
        })

    try:
        from hermes_cli.auth import PROVIDER_REGISTRY  # type: ignore

        for pid, provider in PROVIDER_REGISTRY.items():
            base_url = ""
            env_var = getattr(provider, "base_url_env_var", "") or ""
            if env_var:
                base_url = os.environ.get(env_var, "") or ""
            if not base_url:
                base_url = getattr(provider, "inference_base_url", "") or ""
            _add_url(base_url, f"auto:provider:{pid}")
    except Exception as exc:
        logger.debug("auto_allow_providers: PROVIDER_REGISTRY unavailable (%s)", exc)

    # User-defined custom providers from the config ``providers:`` section.
    providers_section = config.get("providers")
    if isinstance(providers_section, dict):
        for key, entry in providers_section.items():
            if not isinstance(entry, dict):
                continue
            base_url = entry.get("base_url") or entry.get("inference_base_url") or ""
            if isinstance(base_url, str):
                _add_url(base_url, f"auto:provider:{key}")

    # Legacy custom_providers list.
    legacy = config.get("custom_providers")
    if isinstance(legacy, list):
        for entry in legacy:
            if not isinstance(entry, dict):
                continue
            base_url = entry.get("base_url") or ""
            label = entry.get("provider_key") or entry.get("name") or "custom"
            if isinstance(base_url, str):
                _add_url(base_url, f"auto:provider:{label}")

    return rules


def _auto_allow_mcp_hosts(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return rules derived from HTTP-transport MCP servers in config."""
    rules: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    mcp_servers = config.get("mcp_servers")
    if not isinstance(mcp_servers, dict):
        return rules

    for name, entry in mcp_servers.items():
        if not isinstance(entry, dict):
            continue
        url = entry.get("url") or ""
        if not isinstance(url, str) or "://" not in url:
            continue
        parsed = urlparse(url)
        host = _normalize_host(parsed.hostname or "")
        if not host or host in seen:
            continue
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
        seen.add(host)
        rules.append({
            "pattern": host,
            "ports": [port] if port else [],
            "source": f"auto:mcp:{name}",
            "description": f"auto-allowed MCP server '{name}'",
        })
    return rules


def load_network_allowlist(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load and return the parsed network allowlist policy.

    Results are cached for ``_CACHE_TTL_SECONDS`` to avoid re-reading
    ``config.yaml`` on every egress check.  Pass an explicit ``config_path``
    (tests) to bypass the cache.
    """
    global _cached_policy, _cached_policy_path, _cached_policy_time

    resolved_path = str(config_path) if config_path else "__default__"
    now = time.monotonic()

    if config_path is None:
        with _cache_lock:
            if (
                _cached_policy is not None
                and _cached_policy_path == resolved_path
                and (now - _cached_policy_time) < _CACHE_TTL_SECONDS
            ):
                return _cached_policy

    policy_config_path = config_path or _get_default_config_path()
    policy = _load_policy_config(policy_config_path)
    config_root = policy.pop("__config_root__", {}) or {}

    raw_rules = policy.get("rules", []) or []
    if not isinstance(raw_rules, list):
        raise NetworkPolicyError("security.network_allowlist.rules must be a list")

    raw_shared_files = policy.get("shared_files", []) or []
    if not isinstance(raw_shared_files, list):
        raise NetworkPolicyError("security.network_allowlist.shared_files must be a list")

    enabled = policy.get("enabled", False)
    if not isinstance(enabled, bool):
        raise NetworkPolicyError("security.network_allowlist.enabled must be a boolean")

    auto_allow_providers = policy.get("auto_allow_providers", True)
    if not isinstance(auto_allow_providers, bool):
        raise NetworkPolicyError("security.network_allowlist.auto_allow_providers must be a boolean")

    auto_allow_mcp = policy.get("auto_allow_mcp", True)
    if not isinstance(auto_allow_mcp, bool):
        raise NetworkPolicyError("security.network_allowlist.auto_allow_mcp must be a boolean")

    compiled_rules: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, str, Tuple[int, ...]]] = set()

    def _append(rule: Optional[Dict[str, Any]]) -> None:
        if rule is None:
            return
        key = (rule["source"], rule["pattern"], tuple(rule["ports"]))
        if key in seen:
            return
        seen.add(key)
        compiled_rules.append(rule)

    for raw_rule in raw_rules:
        _append(_parse_rule_entry(raw_rule, "config"))

    for shared_file in raw_shared_files:
        if not isinstance(shared_file, str) or not shared_file.strip():
            continue
        path = Path(shared_file).expanduser()
        if not path.is_absolute():
            path = (get_hermes_home() / path).resolve()
        for entry in _iter_shared_file_rules(path):
            _append(entry)

    if auto_allow_providers:
        for entry in _auto_allow_provider_hosts(config_root):
            _append(entry)

    if auto_allow_mcp:
        for entry in _auto_allow_mcp_hosts(config_root):
            _append(entry)

    result = {
        "enabled": enabled,
        "rules": compiled_rules,
        "auto_allow_providers": auto_allow_providers,
        "auto_allow_mcp": auto_allow_mcp,
    }

    if policy_config_path == _get_default_config_path():
        with _cache_lock:
            _cached_policy = result
            _cached_policy_path = "__default__"
            _cached_policy_time = now

    return result


def invalidate_cache() -> None:
    """Force the next ``check_network_egress`` call to re-read config."""
    global _cached_policy
    with _cache_lock:
        _cached_policy = None


def _match_host_against_rule(host: str, pattern: str) -> bool:
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        return fnmatch.fnmatch(host, pattern)
    return host == pattern or host.endswith(f".{pattern}")


def _extract_host_and_port(url: str) -> Tuple[str, Optional[int]]:
    parsed = urlparse(url)
    host = _normalize_host(parsed.hostname or parsed.netloc)
    port = parsed.port
    if not host and "://" not in url:
        schemeless = urlparse(f"//{url}")
        host = _normalize_host(schemeless.hostname or schemeless.netloc)
        port = schemeless.port if port is None else port
        parsed = schemeless
    if port is None:
        scheme = parsed.scheme
        if scheme == "https":
            port = 443
        elif scheme == "http":
            port = 80
    return host, port


def check_network_egress(
    url: str, config_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    """Check whether an outbound URL is permitted by the allowlist.

    Returns ``None`` if egress is allowed, or a dict with block metadata
    (``url``, ``host``, ``port``, ``message``) if denied.

    Never raises on policy errors with the default config path — logs a
    warning and returns ``None`` (fail-open on misconfigured policy, so a
    typo doesn't cut off everything).  Tests pass an explicit ``config_path``
    to get strict error propagation.
    """
    if config_path is None:
        with _cache_lock:
            if _cached_policy is not None and not _cached_policy.get("enabled"):
                return None

    try:
        policy = load_network_allowlist(config_path)
    except NetworkPolicyError:
        if config_path is not None:
            raise
        logger.warning("Network policy config error (failing open) — fix config to restore allowlist")
        return None
    except Exception as exc:
        logger.warning("Unexpected error loading network policy (failing open): %s", exc)
        return None

    if not policy.get("enabled"):
        return None

    host, port = _extract_host_and_port(url)
    if not host:
        # No host means no egress target — allow (e.g. file:// paths).
        return None

    for rule in policy.get("rules", []):
        pattern = rule.get("pattern", "")
        if not _match_host_against_rule(host, pattern):
            continue
        rule_ports = rule.get("ports") or []
        if rule_ports and port is not None and port not in rule_ports:
            continue
        return None  # allowed

    logger.info("Denied network egress to %s (host=%s port=%s) — no allowlist match", url, host, port)
    return {
        "url": url,
        "host": host,
        "port": port,
        "rule": "network_allowlist",
        "source": "network_allowlist",
        "message": (
            f"Blocked by network allowlist: '{host}'"
            f"{f':{port}' if port else ''} is not in the permitted list"
        ),
    }
