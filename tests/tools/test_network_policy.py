"""Tests for the allowlist-based network egress policy."""

from __future__ import annotations

import json

import pytest
import yaml

from tools.network_policy import (
    NetworkPolicyError,
    check_network_egress,
    invalidate_cache,
    load_network_allowlist,
)


def _write_config(path, policy):
    path.write_text(
        yaml.safe_dump({"security": {"network_allowlist": policy}}, sort_keys=False),
        encoding="utf-8",
    )


def test_default_config_exposes_network_allowlist_shape():
    from hermes_cli.config import DEFAULT_CONFIG

    allowlist = DEFAULT_CONFIG["security"]["network_allowlist"]
    assert allowlist["enabled"] is False
    assert allowlist["rules"] == []
    assert allowlist["shared_files"] == []
    assert allowlist["auto_allow_providers"] is True
    assert allowlist["auto_allow_mcp"] is True


def test_disabled_allowlist_permits_everything(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, {"enabled": False})

    assert check_network_egress("https://random.example", config_path=config_path) is None


def test_enabled_with_empty_rules_denies_everything(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "enabled": True,
            "rules": [],
            "auto_allow_providers": False,
            "auto_allow_mcp": False,
        },
    )

    blocked = check_network_egress("https://anything.example", config_path=config_path)
    assert blocked is not None
    assert blocked["host"] == "anything.example"
    assert "Blocked by network allowlist" in blocked["message"]


def test_domain_rule_allows_matching_host(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "enabled": True,
            "rules": [{"domain": "example.com", "ports": [443]}],
            "auto_allow_providers": False,
            "auto_allow_mcp": False,
        },
    )

    assert check_network_egress("https://example.com/x", config_path=config_path) is None
    assert check_network_egress("https://api.example.com", config_path=config_path) is None


def test_port_filter_blocks_mismatched_ports(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "enabled": True,
            "rules": [{"domain": "example.com", "ports": [443]}],
            "auto_allow_providers": False,
            "auto_allow_mcp": False,
        },
    )

    blocked = check_network_egress("http://example.com", config_path=config_path)
    assert blocked is not None
    assert blocked["port"] == 80


def test_wildcard_subdomain_pattern(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "enabled": True,
            "rules": [{"domain": "*.example.com"}],
            "auto_allow_providers": False,
            "auto_allow_mcp": False,
        },
    )

    assert check_network_egress("https://a.example.com", config_path=config_path) is None
    # Apex doesn't match *.example.com
    assert check_network_egress("https://example.com", config_path=config_path) is not None


def test_string_rule_shorthand(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "enabled": True,
            "rules": ["example.com"],
            "auto_allow_providers": False,
            "auto_allow_mcp": False,
        },
    )

    assert check_network_egress("https://example.com", config_path=config_path) is None
    assert check_network_egress("https://other.test", config_path=config_path) is not None


def test_shared_file_rules_merged(tmp_path):
    shared = tmp_path / "allowlist.txt"
    shared.write_text("# comment\ntrusted.example\n", encoding="utf-8")

    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "enabled": True,
            "shared_files": [str(shared)],
            "auto_allow_providers": False,
            "auto_allow_mcp": False,
        },
    )

    assert check_network_egress("https://trusted.example", config_path=config_path) is None
    assert check_network_egress("https://nope.example", config_path=config_path) is not None


def test_missing_shared_file_is_warned_not_fatal(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "enabled": True,
            "shared_files": [str(tmp_path / "does-not-exist.txt")],
            "rules": ["example.com"],
            "auto_allow_providers": False,
            "auto_allow_mcp": False,
        },
    )

    # Policy loads; the domain in rules still applies, missing shared file skipped.
    assert check_network_egress("https://example.com", config_path=config_path) is None


def test_auto_allow_mcp_http_servers(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "security": {
                    "network_allowlist": {
                        "enabled": True,
                        "auto_allow_providers": False,
                        "auto_allow_mcp": True,
                    }
                },
                "mcp_servers": {
                    "remote": {"url": "https://mcp.example.com/mcp"},
                    "local": {"command": "npx", "args": ["server"]},  # ignored
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    assert check_network_egress("https://mcp.example.com/path", config_path=config_path) is None
    assert check_network_egress("https://other.example", config_path=config_path) is not None


def test_auto_allow_mcp_disabled(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "security": {
                    "network_allowlist": {
                        "enabled": True,
                        "auto_allow_providers": False,
                        "auto_allow_mcp": False,
                    }
                },
                "mcp_servers": {"remote": {"url": "https://mcp.example.com/mcp"}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    assert check_network_egress("https://mcp.example.com/path", config_path=config_path) is not None


def test_auto_allow_custom_providers(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "security": {
                    "network_allowlist": {
                        "enabled": True,
                        "auto_allow_providers": True,
                        "auto_allow_mcp": False,
                    }
                },
                "providers": {
                    "my-local": {"base_url": "http://localhost:8000/v1"},
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    assert check_network_egress("http://localhost:8000/v1/chat", config_path=config_path) is None


def test_load_network_allowlist_rejects_non_list_rules(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, {"enabled": True, "rules": "example.com"})

    with pytest.raises(NetworkPolicyError, match="rules must be a list"):
        load_network_allowlist(config_path)


def test_load_network_allowlist_rejects_non_bool_enabled(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, {"enabled": "yes"})

    with pytest.raises(NetworkPolicyError, match="enabled must be a boolean"):
        load_network_allowlist(config_path)


def test_load_network_allowlist_rejects_non_bool_auto_allow_providers(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path, {"enabled": True, "auto_allow_providers": "yes"})

    with pytest.raises(NetworkPolicyError, match="auto_allow_providers must be a boolean"):
        load_network_allowlist(config_path)


def test_load_network_allowlist_missing_section_returns_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump({"display": {"theme": "dark"}}), encoding="utf-8")

    policy = load_network_allowlist(config_path)
    assert policy["enabled"] is False
    assert policy["rules"] == []


def test_check_network_egress_allows_scheme_less_urls(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "enabled": True,
            "rules": ["example.com"],
            "auto_allow_providers": False,
            "auto_allow_mcp": False,
        },
    )

    assert check_network_egress("example.com/path", config_path=config_path) is None
    assert check_network_egress("other.test", config_path=config_path) is not None


def test_check_network_egress_fails_open_on_malformed_default_config(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("security: [oops\n", encoding="utf-8")

    # Explicit path: error propagates.
    with pytest.raises(NetworkPolicyError):
        check_network_egress("https://example.com", config_path=config_path)

    # Default path: errors swallowed, fail-open.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    invalidate_cache()
    assert check_network_egress("https://example.com") is None


def test_browser_navigate_returns_network_policy_block(monkeypatch):
    from tools import browser_tool

    monkeypatch.setattr(browser_tool, "_is_safe_url", lambda url: True)
    monkeypatch.setattr(browser_tool, "check_website_access", lambda url: None)
    monkeypatch.setattr(
        browser_tool,
        "check_network_egress",
        lambda url: {
            "host": "denied.test",
            "rule": "network_allowlist",
            "source": "network_allowlist",
            "message": "Blocked by network allowlist: 'denied.test' is not in the permitted list",
            "port": 443,
            "url": url,
        },
    )
    monkeypatch.setattr(
        browser_tool,
        "_run_browser_command",
        lambda *args, **kwargs: pytest.fail("browser command should not run for blocked URL"),
    )

    result = json.loads(browser_tool.browser_navigate("https://denied.test"))

    assert result["success"] is False
    assert "network allowlist" in result["error"]
    assert result["blocked_by_policy"]["rule"] == "network_allowlist"


@pytest.mark.asyncio
async def test_web_extract_blocked_by_network_policy(monkeypatch):
    from tools import web_tools

    monkeypatch.setattr(web_tools, "is_safe_url", lambda url: True)
    monkeypatch.setattr(web_tools, "check_website_access", lambda url: None)
    monkeypatch.setattr(
        web_tools,
        "check_network_egress",
        lambda url: {
            "host": "denied.test",
            "rule": "network_allowlist",
            "source": "network_allowlist",
            "message": "Blocked by network allowlist",
            "port": 443,
            "url": url,
        },
    )
    monkeypatch.setattr(
        web_tools,
        "_get_firecrawl_client",
        lambda: pytest.fail("firecrawl should not run for egress-blocked URL"),
    )
    monkeypatch.setattr("tools.interrupt.is_interrupted", lambda: False)

    result = json.loads(
        await web_tools.web_extract_tool(["https://denied.test"], use_llm_processing=False)
    )

    assert result["results"][0]["blocked_by_policy"]["rule"] == "network_allowlist"
