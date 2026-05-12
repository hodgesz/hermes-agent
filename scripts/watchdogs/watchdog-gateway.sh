#!/usr/bin/env bash
# Gateway watchdog for hermes cron (--no-agent mode).
#
# Silent on healthy runs (exit 0, stdout empty). On failure, prints a
# one-line alert that the cron delivery layer forwards to the configured
# target (in our case: telegram:$CHAT_ID). Keeps the check cheap — no
# LLM invocation, just a PID lookup via the same path `hermes cron
# status` uses.
#
# Install: copy to ~/.hermes/scripts/ (NOT a symlink — the cron
# scheduler's path-safety check resolves symlinks and rejects anything
# outside HERMES_HOME/scripts/).
#   cp scripts/watchdogs/watchdog-gateway.sh ~/.hermes/scripts/
#   chmod +x ~/.hermes/scripts/watchdog-gateway.sh
#
# Healthy  = find_gateway_pids() returns at least one PID
# Unhealthy = empty PID list, OR subprocess error trying to check
set -euo pipefail

HERMES_PY="/Users/hodgesz/VsCodeProjects/hermes-agent/.venv/bin/python"

if [[ ! -x "$HERMES_PY" ]]; then
  echo "watchdog-gateway: hermes venv python missing at $HERMES_PY"
  exit 0
fi

# Use Python to reuse the exact detection logic `hermes cron status`
# relies on. Any exception is treated as a detection failure and
# surfaced so we don't hide a broken check behind an "unhealthy" signal.
PIDS=$("$HERMES_PY" - <<'PY' 2>/dev/null || echo "__ERR__"
try:
    from hermes_cli.gateway import find_gateway_pids
    pids = find_gateway_pids()
    print(",".join(str(p) for p in pids))
except Exception as exc:
    print(f"__ERR__:{exc}")
PY
)

if [[ "$PIDS" == __ERR__* ]]; then
  echo "🚨 Hermes watchdog: gateway PID probe failed (${PIDS})"
  exit 0
fi

if [[ -z "$PIDS" ]]; then
  echo "🚨 Hermes watchdog: gateway is NOT running (no PID found)"
  echo "Fix: hermes gateway restart"
  exit 0
fi

# Healthy — stay silent so the cron job's delivery layer suppresses the
# Telegram send (see `no_agent + empty stdout` semantics in cron/scheduler.py).
exit 0
