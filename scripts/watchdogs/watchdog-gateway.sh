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

# Source ~/.hermes/.env so TELEGRAM_BOT_TOKEN and TELEGRAM_HOME_CHANNEL are
# available when running under launchd (no shell profile).
if [[ -f "$HOME/.hermes/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=${value}"
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$HOME/.hermes/.env" || true)
fi

HERMES_PY="/Users/hodgesz/VsCodeProjects/hermes-agent/.venv/bin/python"

# Deliver an alert message to Telegram (if credentials are available),
# then also print to stdout for the launchd log.
send_alert() {
  local msg="$1"
  echo "$msg"
  if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_HOME_CHANNEL:-}" ]]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${TELEGRAM_HOME_CHANNEL}" \
      --data-urlencode "text=${msg}" >/dev/null || true
  fi
}

if [[ ! -x "$HERMES_PY" ]]; then
  send_alert "🚨 Hermes watchdog: hermes venv python missing at $HERMES_PY"
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
  send_alert "🚨 Hermes watchdog: gateway PID probe failed (${PIDS})"
  exit 0
fi

if [[ -z "$PIDS" ]]; then
  send_alert "🚨 Hermes watchdog: gateway is NOT running (no PID found)
Fix: hermes gateway restart"
  exit 0
fi

# Healthy — stay silent so the cron job's delivery layer suppresses the
# Telegram send (see `no_agent + empty stdout` semantics in cron/scheduler.py).
exit 0
