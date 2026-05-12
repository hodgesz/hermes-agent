#!/usr/bin/env bash
# LiteLLM proxy watchdog for hermes cron (--no-agent mode).
#
# Silent on healthy runs. Alerts when:
#   - /health is unreachable (curl fails)
#   - /health reports any unhealthy endpoint
#   - a minimal chat completion through the proxy fails
#
# The briefing + every agent call routes through this proxy, so a dead
# LiteLLM manifests as "LLM errors" all over the place. The live-probe
# is what actually catches Bedrock auth expiry, model config drift,
# etc. — /health alone doesn't see those until something hits them.
#
# Install: copy to ~/.hermes/scripts/ (NOT a symlink — the cron
# scheduler's path-safety check resolves symlinks and rejects anything
# outside HERMES_HOME/scripts/).
#   cp scripts/watchdogs/watchdog-litellm.sh ~/.hermes/scripts/
#   chmod +x ~/.hermes/scripts/watchdog-litellm.sh
set -euo pipefail

HEALTH_URL="http://localhost:4000/health"
CHAT_URL="http://localhost:4000/v1/chat/completions"
MODEL="claude-sonnet-4-6"

# 1. /health endpoint reachability
RESPONSE=$(curl -fsS --max-time 5 "$HEALTH_URL" 2>&1) || {
  echo "🚨 LiteLLM watchdog: proxy unreachable at ${HEALTH_URL}"
  echo "curl: $RESPONSE"
  exit 0
}

UNHEALTHY=$(echo "$RESPONSE" | /usr/bin/python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    print(int(data.get("unhealthy_count", 0)))
except Exception as e:
    # Treat parse failure as a watchdog-worthy event — /health usually
    # returns JSON, so malformed output itself is a signal.
    print(f"parse_error:{e}")
' 2>/dev/null || echo "parse_error")

if [[ "$UNHEALTHY" == parse_error* ]]; then
  echo "🚨 LiteLLM watchdog: /health returned unexpected payload (${UNHEALTHY})"
  echo "Raw: ${RESPONSE:0:200}"
  exit 0
fi

if [[ "$UNHEALTHY" -gt 0 ]]; then
  echo "🚨 LiteLLM watchdog: ${UNHEALTHY} unhealthy endpoint(s) reported"
  FIRST=$(echo "$RESPONSE" | /usr/bin/python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    ep = (data.get("unhealthy_endpoints") or [{}])[0]
    print(ep.get("model", "?"))
except Exception:
    pass
' 2>/dev/null || true)
  [[ -n "$FIRST" ]] && echo "First unhealthy model: ${FIRST}"
  exit 0
fi

# 2. Live completion probe — catches upstream auth/config drift that
# /health doesn't see. Keep the token budget tiny so this is cheap.
PROBE=$(curl -s --max-time 15 -X POST "$CHAT_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dummy" \
  -d '{"model":"'"$MODEL"'","max_tokens":5,"messages":[{"role":"user","content":"reply ok"}]}' 2>&1) || {
  echo "🚨 LiteLLM watchdog: live completion probe failed to connect"
  echo "curl: $PROBE"
  exit 0
}

PROBE_OK=$(echo "$PROBE" | /usr/bin/python3 -c '
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

if [[ "$PROBE_OK" != "ok" ]]; then
  echo "🚨 LiteLLM watchdog: ${MODEL} completion probe failed"
  echo "Reason: ${PROBE_OK}"
  exit 0
fi

# Everything green — silent.
exit 0
