#!/usr/bin/env bash
# Hermes launchd job runner — invokes a Hermes skill one-shot and delivers
# the agent response to Telegram. Writes an atomic status file that the
# companion health-check script monitors for staleness.
#
# Usage:
#   hermes-briefing.sh --skill <skill-name> [--chat-id <id>] [--job <label>]
#                      [--prompt <text>]
#
# Required env: TELEGRAM_BOT_TOKEN (or TELEGRAM_HOME_CHANNEL if --chat-id omitted).
# All env is sourced from ~/.hermes/.env since launchd has no shell profile.

set -euo pipefail

HERMES_REPO="/Users/hodgesz/VsCodeProjects/hermes-agent"
HERMES_PY="${HERMES_REPO}/.venv/bin/python"
HERMES_CLI="${HERMES_REPO}/hermes"
STATUS_DIR="/tmp"

if [[ ! -x "$HERMES_PY" ]]; then
  echo "hermes venv python missing at $HERMES_PY" >&2
  exit 2
fi

# Source ~/.hermes/.env — launchd provides none of the user's shell env.
# Read only KEY=VALUE lines to keep this safe from accidental shell exec.
if [[ -f "$HOME/.hermes/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=${value}"
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$HOME/.hermes/.env" || true)
fi

# Put node on PATH so npx-launched MCP servers (obsidian/filesystem/peekaboo)
# can resolve their `#!/usr/bin/env node` shebangs. launchd's plist PATH only
# covers Homebrew; user's node lives under nvm. Pick the newest installed
# version and prepend it — no-op when node isn't nvm-managed.
if [[ -d "$HOME/.nvm/versions/node" ]]; then
  NVM_LATEST=$(ls -1 "$HOME/.nvm/versions/node" 2>/dev/null \
    | sort -V | tail -n1)
  if [[ -n "$NVM_LATEST" && -x "$HOME/.nvm/versions/node/$NVM_LATEST/bin/node" ]]; then
    export PATH="$HOME/.nvm/versions/node/$NVM_LATEST/bin:$PATH"
  fi
fi

SKILL=""
CHAT_ID="${TELEGRAM_HOME_CHANNEL:-}"
JOB_LABEL=""
PROMPT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skill)    SKILL="$2"; shift 2 ;;
    --chat-id)  CHAT_ID="$2"; shift 2 ;;
    --job)      JOB_LABEL="$2"; shift 2 ;;
    --prompt)   PROMPT="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$SKILL" ]]; then
  echo "--skill is required" >&2
  exit 2
fi
if [[ -z "${TELEGRAM_BOT_TOKEN:-}" || -z "$CHAT_ID" ]]; then
  echo "TELEGRAM_BOT_TOKEN and chat id (--chat-id or TELEGRAM_HOME_CHANNEL) required" >&2
  exit 2
fi

JOB_LABEL="${JOB_LABEL:-$SKILL}"
# Default prompt mirrors NemoClaw's pattern — one verb, let the skill drive.
# adhd-founder-planner: the skill's interactive commands (plan/migrate/dopamine)
# all open by asking questions, which doesn't work for a scheduled push where
# the user hasn't spoken yet. Use the `push` command for launchd runs — it's
# non-interactive and varies by time of day.
if [[ -z "$PROMPT" ]]; then
  case "$SKILL" in
    morning-briefing)       PROMPT="Give me my morning briefing" ;;
    adhd-founder-planner)
      HOUR=$(date '+%-H')
      if   (( HOUR < 12 )); then PUSH_SLOT="morning kickoff"
      elif (( HOUR < 16 )); then PUSH_SLOT="midday reset"
      else                       PUSH_SLOT="afternoon wind-down"
      fi
      PROMPT="Run the adhd-founder-planner 'push' command for the ${PUSH_SLOT} slot"
      ;;
    personal-crm)           PROMPT="Show me this week's relationship follow-ups" ;;
    *)                      PROMPT="Run the ${SKILL} skill" ;;
  esac
fi

# Anchor today's date in the prompt so the model doesn't rely on training-data
# guesses (observed: briefing reported "Sunday April 19" on an actual Monday).
# Uses the local clock so it matches the user's timezone, not UTC.
TODAY_STR=$(date '+%A, %B %-d, %Y')
PROMPT="Today is ${TODAY_STR}. ${PROMPT}"

# Always run in a fresh one-shot session — never resume the user's DM session.
# Resuming caused the session to grow unboundedly across days, eventually
# triggering context compaction which fails with LiteLLM's HTTP 500
# (Anthropic tool-call format → OpenAI translation bug).
STATUS_FILE="${STATUS_DIR}/hermes-${JOB_LABEL}-status.json"

# Run the agent. Quiet (-Q) strips banner/spinner so we get only the response.
# --source tool keeps it out of the user's session list.
# No --resume: fresh session every run so context never accumulates.
RESPONSE=$(cd "$HERMES_REPO" && "$HERMES_PY" "$HERMES_CLI" chat \
  -Q \
  -q "$PROMPT" \
  -s "$SKILL" \
  --source tool 2>&1 || true)

# Strip CLI/tool UI chrome so the briefing isn't polluted by non-content.
# "↻ Resumed session ..."       — session-resume banner
# "session_id: ..."              — emitted by --pass-session-id
# "✓ [N/M] ..."                  — tool-call progress lines
# Permission prompt blocks       — "DANGEROUS COMMAND", policy prompts,
#                                  [o]nce/[s]ession/[a]lways/[d]eny lines,
#                                  "Choice [o/s/a/D]:", "✗ Denied"/"✓ Approved",
#                                  and the indented code preview that follows
#                                  them. These are TTY chrome that leaks
#                                  through -Q on tool-policy refusals.
RESPONSE=$(echo "$RESPONSE" | python3 -c "
import re, sys
text = sys.stdin.read()
lines = text.splitlines()
out = []
skip_until_blank = False
for line in lines:
    s = line.rstrip()
    if re.match(r'^\[.*\] INFO', s): continue
    if s.startswith('[gateway]'): continue
    if 'UNDICI_' in s: continue
    if s.startswith('(node:'): continue
    if 'DeprecationWarning' in s: continue
    if 'pairing required' in s: continue
    if s.startswith('Loading skill'): continue
    # MCP server startup banners (filesystem MCP prints these to stderr).
    if s.startswith('Secure MCP Filesystem Server running on stdio'): continue
    if s.startswith('Client does not support MCP Roots'): continue
    if s.startswith('↻ Resumed session'): continue
    if s.startswith('messages, '): continue
    if s.startswith('session_id: '): continue
    if re.match(r'^\s*✓ \[\d+/\d+\]', s): continue
    # Inter-turn model commentary from run_agent.py — not final content.
    if re.match(r'^\s*┊ 💬', s): continue
    # Tool progress scrollback lines (search/fetch/etc.) — non-content.
    if re.match(r'^\s*┊ ', s): continue
    if re.search(r'DANGEROUS COMMAND', s) or re.search(r'\[o\]nce\s+\|\s+\[s\]ession', s):
        skip_until_blank = True
        continue
    if re.search(r'Choice \[o/s/a/D\]', s): continue
    if skip_until_blank:
        if s.strip() == '':
            skip_until_blank = False
        continue
    out.append(line)
print('\n'.join(out))
" 2>/dev/null || echo "$RESPONSE")

# Classify outcome so the health check can page when briefings are dying.
BRIEFING_STATUS="ok"
if [[ -z "${RESPONSE// /}" ]]; then
  RESPONSE="⚠️ ${JOB_LABEL} failed — agent returned empty response."
  BRIEFING_STATUS="error:empty"
elif echo "$RESPONSE" | grep -qiE "timed? out|timeout|ETIMEDOUT|request.*(fail|error)|LLM.*(fail|error|timed)|inference.*(fail|error|unavailable)|connection refused|ECONNREFUSED|(^|[^0-9])5(02|03)([^0-9]|$)|rate.limit|Traceback \(most recent|Session not found|Session '.*' not found|Use a session ID|No session found|No previous .* session|provider.*(not configured|missing)|^hermes: error:"; then
  RESPONSE="⚠️ ${JOB_LABEL} failed — agent error:

${RESPONSE}"
  BRIEFING_STATUS="error:llm"
fi

# Atomic status write — health-check reads this every 10 minutes.
TMP_STATUS=$(mktemp "${STATUS_FILE}.XXXXXX")
printf '{"timestamp":"%s","status":"%s","job":"%s","chat_id":"%s"}\n' \
  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$BRIEFING_STATUS" "$JOB_LABEL" "$CHAT_ID" \
  > "$TMP_STATUS"
mv -f "$TMP_STATUS" "$STATUS_FILE"

# Deliver. Markdown first, plain text on failure (LLM output often has
# unbalanced backticks/asterisks that Telegram's Markdown parser rejects).
send_telegram() {
  local parse_mode="$1"
  local args=(-s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage"
              -d "chat_id=${CHAT_ID}"
              --data-urlencode "text=${RESPONSE}")
  [[ -n "$parse_mode" ]] && args+=(-d "parse_mode=${parse_mode}")
  curl "${args[@]}"
}

RESULT=$(send_telegram "Markdown" 2>&1)
if echo "$RESULT" | grep -q '"ok":false'; then
  RESULT=$(send_telegram "" 2>&1)
fi

if echo "$RESULT" | grep -q '"ok":true'; then
  echo "[$(date)] ${JOB_LABEL} delivered to chat ${CHAT_ID} (status: ${BRIEFING_STATUS})"
else
  echo "[$(date)] ${JOB_LABEL} delivery failed: $RESULT" >&2
  TMP_STATUS=$(mktemp "${STATUS_FILE}.XXXXXX")
  printf '{"timestamp":"%s","status":"error:send","job":"%s","chat_id":"%s"}\n' \
    "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$JOB_LABEL" "$CHAT_ID" \
    > "$TMP_STATUS"
  mv -f "$TMP_STATUS" "$STATUS_FILE"
  exit 1
fi
