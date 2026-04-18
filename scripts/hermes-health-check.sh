#!/usr/bin/env bash
# Hermes briefing health check — fires every 10 minutes via launchd.
# Stays silent when everything is fine; alerts Telegram on stale or failed
# scheduled jobs so you don't discover a dead briefing a week later.
#
# Checks every ~/tmp/hermes-*-status.json file for:
#   - file age vs. max_hours (per-job)
#   - last recorded status (ok / error:*)
#
# Usage:
#   hermes-health-check.sh           # print results, alert on failure
#   hermes-health-check.sh --quiet   # exit code only (0 = healthy)

set -euo pipefail

# Per-job staleness threshold (hours). Anything longer = fail.
#   morning-briefing runs daily      -> 25h grace
#   adhd-checkin runs 3x weekdays    -> 72h grace (covers weekend gap)
#   crm-followups runs weekly        -> 170h grace (~7 days + buffer)
declare -A MAX_AGE_HOURS=(
  [morning-briefing]=25
  [adhd-checkin]=72
  [crm-followups]=170
)

QUIET=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quiet) QUIET=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Source credentials the same way hermes-briefing.sh does.
if [[ -f "$HOME/.hermes/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^[[:space:]]*# ]] && continue
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=${value}"
  done < <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$HOME/.hermes/.env" || true)
fi

FAILURES=()

check_job() {
  local job="$1"
  local max_hours="${MAX_AGE_HOURS[$job]:-25}"
  local status_file="/tmp/hermes-${job}-status.json"

  if [[ ! -f "$status_file" ]]; then
    # Not-yet-run is not a failure — could be a fresh install or a
    # not-yet-triggered weekly. Skip rather than alerting.
    [[ $QUIET -eq 0 ]] && echo "skip  ${job}: no status file yet"
    return
  fi

  local status timestamp file_epoch now_epoch age_hours
  status="$(grep -o '"status":"[^"]*"' "$status_file" | head -1 | cut -d'"' -f4 || true)"
  timestamp="$(grep -o '"timestamp":"[^"]*"' "$status_file" | head -1 | cut -d'"' -f4 || true)"

  if [[ -n "$timestamp" ]]; then
    # BSD date on macOS: parse ISO8601 Z format. Fall back to mtime on parse failure.
    file_epoch=$(date -jf '%Y-%m-%dT%H:%M:%SZ' "$timestamp" '+%s' 2>/dev/null \
                 || stat -f '%m' "$status_file")
  else
    file_epoch=$(stat -f '%m' "$status_file")
  fi
  now_epoch=$(date '+%s')
  age_hours=$(( (now_epoch - file_epoch) / 3600 ))

  if [[ "$age_hours" -gt "$max_hours" ]]; then
    FAILURES+=("${job}: stale (${age_hours}h old, limit ${max_hours}h)")
    [[ $QUIET -eq 0 ]] && echo "FAIL  ${job}: stale ${age_hours}h"
    return
  fi

  case "$status" in
    ok)
      [[ $QUIET -eq 0 ]] && echo "ok    ${job}: last run ${timestamp}"
      ;;
    error:*)
      FAILURES+=("${job}: ${status} at ${timestamp}")
      [[ $QUIET -eq 0 ]] && echo "FAIL  ${job}: ${status}"
      ;;
    *)
      FAILURES+=("${job}: unknown status '${status}'")
      [[ $QUIET -eq 0 ]] && echo "FAIL  ${job}: unknown status"
      ;;
  esac
}

for job in "${!MAX_AGE_HOURS[@]}"; do
  check_job "$job"
done

if [[ ${#FAILURES[@]} -eq 0 ]]; then
  exit 0
fi

# Alert to Telegram. Only fires on failure — no noise on healthy runs.
if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_HOME_CHANNEL:-}" ]]; then
  MSG="⚠️ Hermes health check: $(date '+%Y-%m-%d %H:%M')"$'\n\n'
  for fail in "${FAILURES[@]}"; do
    MSG+="• ${fail}"$'\n'
  done
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d "chat_id=${TELEGRAM_HOME_CHANNEL}" \
    --data-urlencode "text=${MSG}" >/dev/null || true
fi

exit 1
