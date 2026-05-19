#!/usr/bin/env bash
# LiteLLM proxy watchdog.
#
# Quiet by default. Alerts to Telegram only after N consecutive failures
# (default 3, env: WATCHDOG_FAILURE_THRESHOLD). Bedrock has occasional
# transient 503s and connection blips that recover within a couple of
# minutes — alerting on every single bad probe was producing 15+
# Telegram messages per day for things that didn't need attention.
#
# Failure causes monitored:
#   - /health endpoint unreachable
#   - /health reports an unhealthy endpoint
#   - live chat-completion probe fails (catches auth/config drift +
#     upstream Bedrock outages that /health doesn't see)
#
# Behaviour:
#   - Healthy probe → reset counter, send recovery message ONCE if we
#     had previously alerted, otherwise stay silent
#   - Failed probe  → increment counter, alert ONCE when counter first
#     reaches the threshold, stay silent on subsequent failures until
#     recovery
#
# State persists between runs in $HOME/.hermes/watchdog-litellm.state
# (JSON — `consecutive_failures` + `alerted`).
#
# Install: copy to ~/.hermes/scripts/ (NOT a symlink — cron's path-safety
# check resolves symlinks and rejects anything outside HERMES_HOME/scripts/).
#   cp scripts/watchdogs/watchdog-litellm.sh ~/.hermes/scripts/
#   chmod +x ~/.hermes/scripts/watchdog-litellm.sh
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

HEALTH_URL="http://localhost:4000/health"
CHAT_URL="http://localhost:4000/v1/chat/completions"
MODEL="claude-sonnet-4-6"
STATE_FILE="$HOME/.hermes/watchdog-litellm.state"
THRESHOLD="${WATCHDOG_FAILURE_THRESHOLD:-3}"

# ── Telegram alerting ────────────────────────────────────────────
send_telegram() {
  local msg="$1"
  echo "$msg"
  if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_HOME_CHANNEL:-}" ]]; then
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${TELEGRAM_HOME_CHANNEL}" \
      --data-urlencode "text=${msg}" >/dev/null || true
  fi
}

# ── State helpers (consecutive_failures + alerted flag) ─────────
# Use python3 for atomic JSON read/write so concurrent runs don't corrupt.
read_state() {
  /usr/bin/python3 - "$STATE_FILE" <<'PY' 2>/dev/null || echo "0 false"
import json, sys
try:
    with open(sys.argv[1]) as f:
        s = json.load(f)
    print(int(s.get("consecutive_failures", 0)), str(s.get("alerted", False)).lower())
except Exception:
    print("0 false")
PY
}

write_state() {
  local failures="$1" alerted="$2"
  /usr/bin/python3 - "$STATE_FILE" "$failures" "$alerted" <<'PY' 2>/dev/null || true
import json, sys
state_file, failures, alerted = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "true"
with open(state_file, "w") as f:
    json.dump({"consecutive_failures": failures, "alerted": alerted}, f)
PY
}

read -r PREV_FAILURES PREV_ALERTED <<<"$(read_state)"

# ── Probe sequence: short-circuits to set $REASON on first failure ──
REASON=""

probe_health() {
  local response
  response=$(curl -fsS --max-time 5 "$HEALTH_URL" 2>&1) || {
    REASON="proxy unreachable at ${HEALTH_URL} (curl: ${response:0:160})"
    return 1
  }

  local unhealthy
  unhealthy=$(echo "$response" | /usr/bin/python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(int(data.get("unhealthy_count", 0)))
except Exception as e:
    print(f"parse_error:{e}")
' 2>/dev/null || echo "parse_error")

  if [[ "$unhealthy" == parse_error* ]]; then
    REASON="/health returned unexpected payload (${unhealthy}; raw: ${response:0:160})"
    return 1
  fi
  if [[ "$unhealthy" -gt 0 ]]; then
    local first
    first=$(echo "$response" | /usr/bin/python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    ep = (data.get("unhealthy_endpoints") or [{}])[0]
    print(ep.get("model", "?"))
except Exception:
    pass
' 2>/dev/null || true)
    REASON="${unhealthy} unhealthy endpoint(s) reported (first: ${first:-?})"
    return 1
  fi
  return 0
}

probe_completion() {
  local probe probe_ok
  probe=$(curl -s --max-time 15 -X POST "$CHAT_URL" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer dummy" \
    -d '{"model":"'"$MODEL"'","max_tokens":5,"messages":[{"role":"user","content":"reply ok"}]}' 2>&1) || {
    REASON="live completion probe curl failed (${probe:0:160})"
    return 1
  }

  probe_ok=$(echo "$probe" | /usr/bin/python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    if "error" in data:
        err = data["error"]
        msg = err.get("message") if isinstance(err, dict) else str(err)
        print(f"api_error:{msg[:160]}")
    elif not data.get("choices"):
        print("api_error:no_choices_in_response")
    else:
        print("ok")
except Exception as e:
    print(f"parse_error:{e}")
' 2>/dev/null || echo "parse_error")

  if [[ "$probe_ok" != "ok" ]]; then
    REASON="${MODEL} completion probe failed: ${probe_ok}"
    return 1
  fi
  return 0
}

# ── Run probes; classify outcome ────────────────────────────────
if probe_health && probe_completion; then
  # Healthy. If we had previously alerted, send a recovery message
  # ONCE and reset state. Otherwise, stay quiet.
  if [[ "$PREV_ALERTED" == "true" ]]; then
    send_telegram "✅ LiteLLM watchdog: recovered after ${PREV_FAILURES} consecutive failure(s)"
  fi
  write_state 0 false
  exit 0
fi

# Failure path. Increment counter, alert iff we just crossed the threshold.
NEW_FAILURES=$((PREV_FAILURES + 1))
if [[ "$NEW_FAILURES" -ge "$THRESHOLD" && "$PREV_ALERTED" != "true" ]]; then
  send_telegram "🚨 LiteLLM watchdog: ${NEW_FAILURES} consecutive failure(s) — ${REASON}"
  write_state "$NEW_FAILURES" true
else
  # Below threshold OR already alerted — stay silent, just record.
  echo "watchdog: failure ${NEW_FAILURES}/${THRESHOLD} — ${REASON}"
  write_state "$NEW_FAILURES" "$PREV_ALERTED"
fi
exit 0
