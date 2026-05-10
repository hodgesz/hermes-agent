"""
MCP tool-call approval gate.

Every MCP tool invocation routes through ``check_mcp_tool()`` before the
underlying MCP server receives the call. Config drives per-server and
per-tool defaults; approval state, prompting, and gateway/CLI dispatch
reuse the primitives in ``tools.approval`` so the UX matches the
shell-approval flow users already know.

Design:
  - Policy resolution: tool-specific override  →  server default
                                               →  global default
                                               →  "allow" (fail-open
                                               for safety of non-dangerous
                                               servers like obsidian when
                                               no config is present).
  - Policy values: "allow"   — auto-approve, no prompt.
                   "require" — prompt user (CLI prompt or gateway /approve).
  - Pattern key: ``mcp:{server}:{tool}``. Sessions can approve at that
                 exact key, or pre-approve via the existing permanent
                 allowlist.
  - Non-interactive bypass: if neither HERMES_INTERACTIVE nor
    HERMES_GATEWAY_SESSION is set (e.g. cron/launchd briefings, batch
    runs), the gate auto-approves. Matches ``check_dangerous_command``'s
    behavior in approval.py — approvals require a human to be present.
  - YOLO / approvals.mode=off: full bypass, same as shell approvals.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# Keys we return in the "approval_required" response. Matches the shape
# check_dangerous_command() uses so gateway adapters can treat both
# uniformly.
_APPROVAL_REQUIRED_STATUS = "approval_required"


def _load_mcp_approval_config() -> dict:
    """Read the ``mcp_approval`` block from ~/.hermes/config.yaml."""
    try:
        from hermes_cli.config import load_config

        config = load_config()
        return config.get("mcp_approval", {}) or {}
    except Exception as e:
        logger.warning("Failed to load mcp_approval config: %s", e)
        return {}


def _resolve_policy(server_name: str, tool_name: str) -> str:
    """Resolve the effective policy for a specific MCP tool.

    Precedence: tool override → server default → global default → "allow".
    """
    cfg = _load_mcp_approval_config()
    servers_cfg = (cfg.get("servers") or {}).get(server_name) or {}
    tools_cfg = servers_cfg.get("tools") or {}

    # Tool-specific override wins.
    if tool_name in tools_cfg:
        return _normalize_policy(tools_cfg[tool_name])

    # Fall back to server default.
    if "default" in servers_cfg:
        return _normalize_policy(servers_cfg["default"])

    # Global default.
    if "default" in cfg:
        return _normalize_policy(cfg["default"])

    return "allow"


def _normalize_policy(value: Any) -> str:
    """Coerce a config value to 'allow' or 'require'."""
    if isinstance(value, bool):
        return "require" if value else "allow"
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("require", "ask", "prompt", "approve"):
            return "require"
        if v in ("allow", "auto", "off", "pass"):
            return "allow"
    return "allow"


def _format_command_preview(server: str, tool: str, args: dict) -> str:
    """Render a human-friendly preview of the MCP call for approval UI."""
    try:
        arg_json = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        arg_json = repr(args)
    # Keep the preview tight — approval prompts already have chrome around them.
    if len(arg_json) > 400:
        arg_json = arg_json[:397] + "..."
    return f"mcp:{server}:{tool} {arg_json}"


def check_mcp_tool(server_name: str, tool_name: str, args: dict,
                   approval_callback=None) -> dict:
    """Approval gate for a single MCP tool invocation.

    Returns a dict mirroring ``check_dangerous_command``:
      {"approved": True,  "message": None, ...}
      {"approved": False, "status": "approval_required", "message": str, ...}
      {"approved": False, "message": "BLOCKED: ...", ...}

    Callers must treat ``approved=False`` as "do not execute this call."
    """
    from tools.approval import (
        approve_permanent,
        approve_session,
        get_current_session_key,
        is_approved,
        is_current_session_yolo_enabled,
        prompt_dangerous_approval,
        save_permanent_allowlist,
        submit_pending,
        _get_approval_config,
        _permanent_approved,
        _gateway_notify_cbs,
        _gateway_queues,
        _lock,
        _ApprovalEntry,
    )

    # --- Bypass conditions (match shell-approval semantics) ---

    # Global kill switch: approvals.mode=off.
    approvals_cfg = _get_approval_config()
    mode = str(approvals_cfg.get("mode", "manual")).strip().lower()
    if mode == "off":
        return {"approved": True, "message": None}

    # YOLO: per-process or per-session.
    if os.getenv("HERMES_YOLO_MODE") or is_current_session_yolo_enabled():
        return {"approved": True, "message": None}

    # Resolve policy from config.
    policy = _resolve_policy(server_name, tool_name)
    if policy == "allow":
        return {"approved": True, "message": None}

    # Past this point the tool is policy-gated. Check session/permanent
    # approvals before prompting.
    pattern_key = f"mcp:{server_name}:{tool_name}"
    description = f"MCP tool call: {server_name}/{tool_name}"
    session_key = get_current_session_key()
    if is_approved(session_key, pattern_key):
        return {"approved": True, "message": None}

    # No user present → don't prompt, don't silently run either. We must
    # fail closed so a cron job can't actuate the desktop.
    is_cli = os.getenv("HERMES_INTERACTIVE")
    is_gateway = os.getenv("HERMES_GATEWAY_SESSION")
    if not is_cli and not is_gateway:
        return {
            "approved": False,
            "message": (
                f"BLOCKED: MCP tool '{pattern_key}' requires user approval "
                f"and no interactive user is available. Do NOT retry."
            ),
            "pattern_key": pattern_key,
            "description": description,
        }

    # --- Gateway path: queue + notify + block until /approve or /deny ---
    # Mirrors the blocking gateway flow in check_all_command_guards.
    if is_gateway:
        import time

        preview = _format_command_preview(server_name, tool_name, args)
        notify_cb = None
        with _lock:
            notify_cb = _gateway_notify_cbs.get(session_key)

        if notify_cb is not None:
            approval_data = {
                "command": preview,
                "pattern_key": pattern_key,
                "pattern_keys": [pattern_key],
                "description": description,
            }
            entry = _ApprovalEntry(approval_data)
            with _lock:
                _gateway_queues.setdefault(session_key, []).append(entry)

            try:
                notify_cb(approval_data)
            except Exception as exc:
                logger.warning("MCP approval notify failed: %s", exc)
                with _lock:
                    queue = _gateway_queues.get(session_key, [])
                    if entry in queue:
                        queue.remove(entry)
                    if not queue:
                        _gateway_queues.pop(session_key, None)
                return {
                    "approved": False,
                    "message": "BLOCKED: Failed to send approval request to user. Do NOT retry.",
                    "pattern_key": pattern_key,
                    "description": description,
                }

            timeout = approvals_cfg.get("gateway_timeout", 300)
            try:
                timeout = int(timeout)
            except (ValueError, TypeError):
                timeout = 300

            try:
                from tools.environments.base import touch_activity_if_due
            except Exception:
                touch_activity_if_due = None

            _now = time.monotonic()
            _deadline = _now + max(timeout, 0)
            _activity_state = {"last_touch": _now, "start": _now}
            resolved = False
            while True:
                _remaining = _deadline - time.monotonic()
                if _remaining <= 0:
                    break
                if entry.event.wait(timeout=min(1.0, _remaining)):
                    resolved = True
                    break
                if touch_activity_if_due is not None:
                    touch_activity_if_due(
                        _activity_state, "waiting for MCP approval"
                    )

            with _lock:
                queue = _gateway_queues.get(session_key, [])
                if entry in queue:
                    queue.remove(entry)
                if not queue:
                    _gateway_queues.pop(session_key, None)

            choice = entry.result
            if not resolved or choice is None or choice == "deny":
                reason = "timed out" if not resolved else "denied by user"
                return {
                    "approved": False,
                    "message": f"BLOCKED: MCP call {reason}. Do NOT retry.",
                    "pattern_key": pattern_key,
                    "description": description,
                }

            if choice == "session":
                approve_session(session_key, pattern_key)
            elif choice == "always":
                approve_session(session_key, pattern_key)
                approve_permanent(pattern_key)
                save_permanent_allowlist(_permanent_approved)
            # "once": no persistence

            return {"approved": True, "message": None,
                    "user_approved": True, "description": description}

        # Gateway session with no notify callback registered (e.g. cron
        # using --source tool). Mirror the shell gate's fallback: surface
        # approval_required so the adapter can decide what to do.
        submit_pending(session_key, {
            "command": _format_command_preview(server_name, tool_name, args),
            "pattern_key": pattern_key,
            "pattern_keys": [pattern_key],
            "description": description,
        })
        return {
            "approved": False,
            "status": _APPROVAL_REQUIRED_STATUS,
            "pattern_key": pattern_key,
            "description": description,
            "message": (
                f"⚠️ MCP call requires approval ({description}). "
                f"Asking the user.\n\n**Call:**\n```\n"
                f"{_format_command_preview(server_name, tool_name, args)}\n```"
            ),
        }

    # --- CLI path: synchronous prompt. ---
    preview = _format_command_preview(server_name, tool_name, args)
    choice = prompt_dangerous_approval(
        preview,
        description,
        approval_callback=approval_callback,
    )
    if choice == "deny":
        return {
            "approved": False,
            "message": (
                f"BLOCKED: User denied MCP call '{pattern_key}'. "
                f"Do NOT retry — the user has explicitly rejected it."
            ),
            "pattern_key": pattern_key,
            "description": description,
        }
    if choice == "session":
        approve_session(session_key, pattern_key)
    elif choice == "always":
        approve_session(session_key, pattern_key)
        approve_permanent(pattern_key)
        save_permanent_allowlist(_permanent_approved)
    # "once" falls through without persistence
    return {"approved": True, "message": None}
