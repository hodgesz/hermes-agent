#!/usr/bin/env bash
# Hermes briefing health check — fires every 10 minutes via launchd.
# Stays silent when everything is fine; alerts Telegram on stale or failed
# scheduled jobs so you don't discover a dead briefing a week later.
#
# Two classes of job are monitored:
#
#   1. Legacy script-based jobs (launchd → scripts/hermes-briefing.sh),
#      which write /tmp/hermes-<job>-status.json. Watched in LEGACY_JOBS.
#
#   2. hermes cron jobs that deliver via the gateway's in-process ticker.
#      These don't write /tmp status files; their evidence is the newest
#      file under ~/.hermes/cron/output/<job_id>/. Watched in CRON_JOBS,
#      keyed by job name (resolved to job_id via ~/.hermes/cron/jobs.json).
#      A filename containing "FAILED" or an "## Error" section inside the
#      output file marks the run as failed.
#
# Usage:
#   hermes-health-check.sh           # print results, alert on failure
#   hermes-health-check.sh --quiet   # exit code only (0 = healthy)

set -euo pipefail

# Legacy script-based jobs, as "job_name:max_age_hours" pairs.
# (macOS ships bash 3.2 — no `declare -A`, so plain parallel arrays.)
# Add entries like "adhd-checkin:72" here when re-enabling those plists.
LEGACY_JOBS=(
)

# hermes cron jobs, as "job_name:max_age_hours" pairs. Name must match the
# `name` field in ~/.hermes/cron/jobs.json exactly.
# morning-briefing runs daily at 7:05 → 25h grace.
CRON_JOBS=(
  "morning-briefing:25"
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
  # Legacy path — launchd+script jobs that write /tmp/hermes-<job>-status.json.
  local job="$1"
  local max_hours="$2"
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

check_cron_job() {
  # hermes cron job — delivered via gateway, evidence is the newest file in
  # ~/.hermes/cron/output/<job_id>/. Resolves <job_id> from jobs.json by name.
  local job_name="$1"
  local max_hours="$2"
  local jobs_json="$HOME/.hermes/cron/jobs.json"
  local output_root="$HOME/.hermes/cron/output"

  if [[ ! -f "$jobs_json" ]]; then
    FAILURES+=("${job_name}: no ~/.hermes/cron/jobs.json (is hermes cron set up?)")
    [[ $QUIET -eq 0 ]] && echo "FAIL  ${job_name}: no cron jobs.json"
    return
  fi

  # Resolve job name → job id via jobs.json. Python stays out of the loop —
  # the file is small and pure-stdlib json keeps the dependency surface tiny.
  local job_id
  job_id=$(HEALTH_JOB_NAME="$job_name" python3 -c '
import json, os, sys
name = os.environ["HEALTH_JOB_NAME"]
try:
    data = json.load(open(os.path.expanduser("~/.hermes/cron/jobs.json")))
except Exception:
    sys.exit(1)
for job in data.get("jobs", []):
    if job.get("name") == name and job.get("enabled", True):
        print(job.get("id", ""))
        break
' 2>/dev/null || true)

  if [[ -z "$job_id" ]]; then
    FAILURES+=("${job_name}: no matching enabled cron job in jobs.json")
    [[ $QUIET -eq 0 ]] && echo "FAIL  ${job_name}: no matching enabled cron job"
    return
  fi

  local out_dir="${output_root}/${job_id}"
  if [[ ! -d "$out_dir" ]]; then
    # Not-yet-run is not a failure — could be a freshly-created job.
    [[ $QUIET -eq 0 ]] && echo "skip  ${job_name}: no cron output dir yet (id=${job_id})"
    return
  fi

  # Newest file by mtime — BSD ls -t sorts newest first.
  local newest
  newest=$(ls -t "$out_dir" 2>/dev/null | head -1)
  if [[ -z "$newest" ]]; then
    [[ $QUIET -eq 0 ]] && echo "skip  ${job_name}: cron output dir empty yet (id=${job_id})"
    return
  fi

  local file_path="${out_dir}/${newest}"
  local file_epoch now_epoch age_hours
  file_epoch=$(stat -f '%m' "$file_path")
  now_epoch=$(date '+%s')
  age_hours=$(( (now_epoch - file_epoch) / 3600 ))

  if [[ "$age_hours" -gt "$max_hours" ]]; then
    FAILURES+=("${job_name}: stale (${age_hours}h old, limit ${max_hours}h, id=${job_id})")
    [[ $QUIET -eq 0 ]] && echo "FAIL  ${job_name}: stale ${age_hours}h (id=${job_id})"
    return
  fi

  # Failure detection: scheduler writes "(FAILED)" in the H1 title and an
  # "## Error" section in the output when a run throws. See run_job in
  # cron/scheduler.py.
  if head -1 "$file_path" | grep -q "(FAILED)"; then
    FAILURES+=("${job_name}: last run FAILED (file=${newest}, id=${job_id})")
    [[ $QUIET -eq 0 ]] && echo "FAIL  ${job_name}: last run FAILED (${newest})"
    return
  fi

  [[ $QUIET -eq 0 ]] && echo "ok    ${job_name}: last run ${newest} (${age_hours}h ago, id=${job_id})"
}

for entry in ${LEGACY_JOBS[@]+"${LEGACY_JOBS[@]}"}; do
  check_job "${entry%%:*}" "${entry##*:}"
done

for entry in ${CRON_JOBS[@]+"${CRON_JOBS[@]}"}; do
  check_cron_job "${entry%%:*}" "${entry##*:}"
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
