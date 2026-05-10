"""Tests for the MCP tool-call approval gate (tools/mcp_approval.py).

These tests stub ``hermes_cli.config.load_config`` so they don't depend on
whatever the user has in ~/.hermes/config.yaml. They also manipulate the
HERMES_INTERACTIVE / HERMES_GATEWAY_SESSION env vars directly, mirroring
what tests/tools/test_approval.py does for the sibling shell gate.
"""

from __future__ import annotations

import os
import threading
import time
from unittest.mock import patch as mock_patch

import pytest

import tools.approval as approval_module
from tools.mcp_approval import (
    _format_command_preview,
    _normalize_policy,
    _resolve_policy,
    check_mcp_tool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def interactive_cli(monkeypatch):
    """Simulate an interactive CLI user. Clears gateway/yolo flags."""
    monkeypatch.setenv("HERMES_INTERACTIVE", "1")
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)


@pytest.fixture
def gateway_session(monkeypatch):
    """Simulate a gateway session. Clears CLI/yolo flags."""
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.setenv("HERMES_GATEWAY_SESSION", "1")
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)


@pytest.fixture
def non_interactive(monkeypatch):
    """Simulate a cron/launchd context — no user present."""
    monkeypatch.delenv("HERMES_INTERACTIVE", raising=False)
    monkeypatch.delenv("HERMES_GATEWAY_SESSION", raising=False)
    monkeypatch.delenv("HERMES_YOLO_MODE", raising=False)


@pytest.fixture
def clean_session_state():
    """Wipe per-process approval state between tests so they don't bleed."""
    with approval_module._lock:
        approval_module._session_approved.clear()
        approval_module._session_yolo.clear()
        approval_module._pending.clear()
        approval_module._gateway_queues.clear()
        approval_module._gateway_notify_cbs.clear()
        approval_module._permanent_approved.clear()
    yield
    with approval_module._lock:
        approval_module._session_approved.clear()
        approval_module._session_yolo.clear()
        approval_module._pending.clear()
        approval_module._gateway_queues.clear()
        approval_module._gateway_notify_cbs.clear()
        approval_module._permanent_approved.clear()


def _config(**overrides):
    """Build a minimal hermes config dict with the given mcp_approval overrides."""
    base = {"approvals": {"mode": "manual"}, "mcp_approval": overrides}
    return base


# ---------------------------------------------------------------------------
# Policy resolution
# ---------------------------------------------------------------------------


class TestPolicyNormalization:
    def test_allow_variants(self):
        assert _normalize_policy("allow") == "allow"
        assert _normalize_policy("AUTO") == "allow"
        assert _normalize_policy("pass") == "allow"
        assert _normalize_policy(False) == "allow"

    def test_require_variants(self):
        assert _normalize_policy("require") == "require"
        assert _normalize_policy("ask") == "require"
        assert _normalize_policy("prompt") == "require"
        assert _normalize_policy(True) == "require"

    def test_unknown_string_defaults_to_allow(self):
        # Intentional: unknown string should not silently escalate to require.
        assert _normalize_policy("plz") == "allow"


class TestPolicyResolution:
    """Precedence: tool > server > global > 'allow'."""

    def test_tool_override_beats_server_default(self):
        cfg = _config(
            servers={"peekaboo": {"default": "require", "tools": {"image": "allow"}}}
        )
        with mock_patch("hermes_cli.config.load_config", return_value=cfg):
            assert _resolve_policy("peekaboo", "image") == "allow"
            assert _resolve_policy("peekaboo", "click") == "require"

    def test_server_default_applied_to_unlisted_tool(self):
        cfg = _config(servers={"peekaboo": {"default": "require"}})
        with mock_patch("hermes_cli.config.load_config", return_value=cfg):
            assert _resolve_policy("peekaboo", "totally_new_tool") == "require"

    def test_global_default_catches_unknown_server(self):
        cfg = _config(default="require")
        with mock_patch("hermes_cli.config.load_config", return_value=cfg):
            assert _resolve_policy("github", "create_issue") == "require"

    def test_falls_back_to_allow_when_config_empty(self):
        with mock_patch("hermes_cli.config.load_config", return_value={}):
            assert _resolve_policy("peekaboo", "click") == "allow"


# ---------------------------------------------------------------------------
# check_mcp_tool — bypass paths
# ---------------------------------------------------------------------------


class TestBypassConditions:
    """Bypasses should skip the gate entirely (no prompt, approved=True)."""

    def test_approvals_mode_off_bypasses_everything(
        self, interactive_cli, clean_session_state
    ):
        cfg = {"approvals": {"mode": "off"},
               "mcp_approval": {"servers": {"peekaboo": {"default": "require"}}}}
        with mock_patch("hermes_cli.config.load_config", return_value=cfg):
            result = check_mcp_tool(
                "peekaboo", "click", {"target": "Dock"},
                approval_callback=lambda *a, **k: "deny",
            )
            assert result["approved"] is True
            assert result["message"] is None

    def test_yolo_env_var_bypasses_everything(
        self, interactive_cli, clean_session_state, monkeypatch
    ):
        monkeypatch.setenv("HERMES_YOLO_MODE", "1")
        cfg = _config(servers={"peekaboo": {"default": "require"}})
        with mock_patch("hermes_cli.config.load_config", return_value=cfg):
            result = check_mcp_tool(
                "peekaboo", "click", {"target": "Dock"},
                approval_callback=lambda *a, **k: "deny",
            )
            assert result["approved"] is True

    def test_allow_policy_does_not_prompt(
        self, interactive_cli, clean_session_state
    ):
        prompted = []

        def cb(*a, **k):
            prompted.append(True)
            return "deny"

        cfg = _config(
            servers={"peekaboo": {"default": "require", "tools": {"image": "allow"}}}
        )
        with mock_patch("hermes_cli.config.load_config", return_value=cfg):
            result = check_mcp_tool("peekaboo", "image", {}, approval_callback=cb)
            assert result["approved"] is True
            assert not prompted, "allow policy must skip callback"


# ---------------------------------------------------------------------------
# check_mcp_tool — CLI prompt path
# ---------------------------------------------------------------------------


class TestCliPromptPath:
    def _cfg(self):
        return _config(servers={"peekaboo": {"default": "require"}})

    def test_deny_returns_blocked(self, interactive_cli, clean_session_state):
        with mock_patch("hermes_cli.config.load_config", return_value=self._cfg()):
            result = check_mcp_tool(
                "peekaboo", "click", {"target": "Dock"},
                approval_callback=lambda *a, **k: "deny",
            )
        assert result["approved"] is False
        assert "BLOCKED" in result["message"]
        assert "mcp:peekaboo:click" in result["message"]
        assert result["pattern_key"] == "mcp:peekaboo:click"

    def test_once_approves_without_persistence(
        self, interactive_cli, clean_session_state
    ):
        with mock_patch("hermes_cli.config.load_config", return_value=self._cfg()):
            result = check_mcp_tool(
                "peekaboo", "click", {},
                approval_callback=lambda *a, **k: "once",
            )
            assert result["approved"] is True

            # Next call should prompt again — "once" doesn't persist.
            call_count = {"n": 0}

            def prompt_once_more(*a, **k):
                call_count["n"] += 1
                return "deny"

            r2 = check_mcp_tool(
                "peekaboo", "click", {},
                approval_callback=prompt_once_more,
            )
            assert r2["approved"] is False
            assert call_count["n"] == 1, "once should not persist to session"

    def test_session_approval_persists_for_same_key(
        self, interactive_cli, clean_session_state
    ):
        with mock_patch("hermes_cli.config.load_config", return_value=self._cfg()):
            r1 = check_mcp_tool(
                "peekaboo", "click", {},
                approval_callback=lambda *a, **k: "session",
            )
            assert r1["approved"] is True

            # Second call must NOT invoke the callback — already session-approved.
            prompted = []
            r2 = check_mcp_tool(
                "peekaboo", "click", {"different": "args"},
                approval_callback=lambda *a, **k: prompted.append(True) or "deny",
            )
            assert r2["approved"] is True
            assert not prompted

    def test_session_approval_scoped_to_same_tool(
        self, interactive_cli, clean_session_state
    ):
        """Approving peekaboo/click must NOT auto-approve peekaboo/type."""
        with mock_patch("hermes_cli.config.load_config", return_value=self._cfg()):
            check_mcp_tool(
                "peekaboo", "click", {},
                approval_callback=lambda *a, **k: "session",
            )
            prompted = []
            check_mcp_tool(
                "peekaboo", "type", {"text": "hello"},
                approval_callback=lambda *a, **k: prompted.append(True) or "deny",
            )
            assert prompted == [True], "different tool must prompt again"

    def test_always_adds_to_permanent_allowlist(
        self, interactive_cli, clean_session_state
    ):
        with mock_patch("hermes_cli.config.load_config", return_value=self._cfg()), \
             mock_patch("tools.approval.save_permanent_allowlist") as mock_save:
            check_mcp_tool(
                "peekaboo", "click", {},
                approval_callback=lambda *a, **k: "always",
            )
            mock_save.assert_called_once()
            # Permanent set should now contain our key.
            assert "mcp:peekaboo:click" in approval_module._permanent_approved


# ---------------------------------------------------------------------------
# check_mcp_tool — non-interactive (cron/launchd) path
# ---------------------------------------------------------------------------


class TestNonInteractiveFailClosed:
    def test_require_tool_blocks_without_user(
        self, non_interactive, clean_session_state
    ):
        """Cron MUST NOT drive the desktop silently."""
        cfg = _config(servers={"peekaboo": {"default": "require"}})
        with mock_patch("hermes_cli.config.load_config", return_value=cfg):
            result = check_mcp_tool("peekaboo", "click", {})
        assert result["approved"] is False
        assert "no interactive user" in result["message"]
        assert result["pattern_key"] == "mcp:peekaboo:click"

    def test_allow_tool_still_runs_in_non_interactive(
        self, non_interactive, clean_session_state
    ):
        """Read-only tools should still work in cron (e.g. a screenshot briefing)."""
        cfg = _config(servers={"peekaboo": {"tools": {"image": "allow"}}})
        with mock_patch("hermes_cli.config.load_config", return_value=cfg):
            result = check_mcp_tool("peekaboo", "image", {})
        assert result["approved"] is True


# ---------------------------------------------------------------------------
# check_mcp_tool — gateway (blocking queue) path
# ---------------------------------------------------------------------------


class TestGatewayPath:
    """Gateway path registers a notify callback, queues an _ApprovalEntry,
    and blocks until the caller resolves the entry via approval_module
    internals (mirroring how /approve and /deny work in the real gateway).
    """

    def _cfg(self):
        return _config(servers={"peekaboo": {"default": "require"}})

    def _resolve_after(self, session_key, choice, delay=0.05):
        """Helper: wait briefly, then resolve the oldest queued approval."""
        def _worker():
            time.sleep(delay)
            with approval_module._lock:
                queue = approval_module._gateway_queues.get(session_key, [])
                if queue:
                    entry = queue[0]
            if queue:
                entry.result = choice
                entry.event.set()
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return t

    def test_gateway_approve_session_unblocks_caller(
        self, gateway_session, clean_session_state
    ):
        session_key = approval_module.get_current_session_key()
        calls = []
        approval_module._gateway_notify_cbs[session_key] = lambda data: calls.append(data)

        with mock_patch("hermes_cli.config.load_config", return_value=self._cfg()):
            self._resolve_after(session_key, "session")
            result = check_mcp_tool("peekaboo", "click", {"target": "Dock"})

        assert result["approved"] is True
        assert result.get("user_approved") is True
        assert calls, "gateway notify callback should have been invoked"
        assert calls[0]["pattern_key"] == "mcp:peekaboo:click"

    def test_gateway_deny_returns_blocked(
        self, gateway_session, clean_session_state
    ):
        session_key = approval_module.get_current_session_key()
        approval_module._gateway_notify_cbs[session_key] = lambda data: None

        with mock_patch("hermes_cli.config.load_config", return_value=self._cfg()):
            self._resolve_after(session_key, "deny")
            result = check_mcp_tool("peekaboo", "click", {})

        assert result["approved"] is False
        assert "denied by user" in result["message"]

    def test_gateway_timeout_blocks(
        self, gateway_session, clean_session_state
    ):
        """No /approve or /deny — entry event stays unset until deadline."""
        session_key = approval_module.get_current_session_key()
        approval_module._gateway_notify_cbs[session_key] = lambda data: None

        cfg = self._cfg()
        cfg["approvals"]["gateway_timeout"] = 1  # 1-second deadline

        with mock_patch("hermes_cli.config.load_config", return_value=cfg):
            start = time.monotonic()
            result = check_mcp_tool("peekaboo", "click", {})
            elapsed = time.monotonic() - start

        assert result["approved"] is False
        assert "timed out" in result["message"]
        assert elapsed < 3.0, f"timeout should be ~1s, took {elapsed:.1f}s"

    def test_gateway_without_notify_callback_surfaces_approval_required(
        self, gateway_session, clean_session_state
    ):
        """Legacy fallback: when no /approve callback is registered, the caller
        receives an approval_required status so the adapter can decide what to do."""
        with mock_patch("hermes_cli.config.load_config", return_value=self._cfg()):
            result = check_mcp_tool("peekaboo", "click", {"target": "Dock"})
        assert result["approved"] is False
        assert result.get("status") == "approval_required"
        assert result["pattern_key"] == "mcp:peekaboo:click"


# ---------------------------------------------------------------------------
# Preview formatter (used in approval UI)
# ---------------------------------------------------------------------------


class TestCommandPreview:
    def test_preview_includes_server_and_tool(self):
        preview = _format_command_preview("peekaboo", "click", {"x": 100, "y": 200})
        assert preview.startswith("mcp:peekaboo:click")
        assert '"x": 100' in preview and '"y": 200' in preview

    def test_preview_truncates_long_args(self):
        big = {"payload": "A" * 1000}
        preview = _format_command_preview("srv", "tool", big)
        assert preview.endswith("...")
        assert len(preview) <= 450  # server+tool prefix + 400 char cap
