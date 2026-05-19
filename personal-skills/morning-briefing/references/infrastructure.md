# Morning Briefing Infrastructure

## Stack Topology (as of May 2026)

### LiteLLM Proxy
- **Process:** `/Users/hodgesz/.local/bin/litellm --model bedrock/us.anthropic.claude-sonnet-4-6 --port 4000`
- **Managed by:** `~/Library/LaunchAgents/com.nemoclaw.litellm.plist` (launchd, KeepAlive=true, ThrottleInterval=10)
- **Log:** `/tmp/litellm-launchd.log`
- **Credentials:** Static IAM keys in `~/.aws/credentials [default]` (`AKIA...`)
  - NOT SSO — tokens do NOT expire. `aws sts get-caller-identity` SSO errors are irrelevant noise.
- **Hermes config:** `provider: custom`, `base_url: http://localhost:4000/v1`, `api_key: dummy`

### Morning Briefing Job
- **Managed by:** `~/Library/LaunchAgents/com.hermes.morning-briefing.plist` (launchd, `StartCalendarInterval` 7:05 AM)
- **Script:** `hermes-agent/scripts/hermes-briefing.sh` — runs `hermes -z` one-shot, delivers to Telegram via Bot API
- **Output log:** `/tmp/hermes-morning-briefing.log`
- **Status file:** `/tmp/hermes-morning-briefing-status.json` (read by health-check)

### Watchdog Jobs (all launchd)
- **litellm-watchdog** — `~/Library/LaunchAgents/com.hermes.litellm-watchdog.plist`
  - `StartInterval: 600` (10 min), `RunAtLoad: false`
  - Script: `~/.hermes/scripts/watchdog-litellm.sh`
  - Checks `/health` endpoint + live completion probe against `claude-sonnet-4-6`
  - Sends Telegram alert directly via Bot API on failure (sources `~/.hermes/.env` for credentials)
  - Log: `/tmp/hermes-litellm-watchdog.log`
  - Canonical source: `hermes-agent/scripts/watchdogs/watchdog-litellm.sh`
- **gateway-watchdog** — `~/Library/LaunchAgents/com.hermes.gateway-watchdog.plist`
  - `StartInterval: 600` (10 min), `RunAtLoad: false`
  - Script: `~/.hermes/scripts/watchdog-gateway.sh`
  - Checks gateway PID via `hermes_cli.gateway.find_gateway_pids()`
  - Sends Telegram alert directly via Bot API on failure
  - Log: `/tmp/hermes-gateway-watchdog.log`
  - Canonical source: `hermes-agent/scripts/watchdogs/watchdog-gateway.sh`
- **health-check** — `~/Library/LaunchAgents/com.hermes.health-check.plist`
  - `StartInterval: 600` (10 min), `RunAtLoad: false`
  - Script: `hermes-agent/scripts/hermes-health-check.sh`
  - Monitors staleness of scheduled jobs via status files

### Active launchd plists (com.hermes.*)
```
com.hermes.morning-briefing.plist    — briefing, 7:05 AM
com.hermes.litellm-watchdog.plist    — LiteLLM health, every 10m
com.hermes.gateway-watchdog.plist    — gateway PID, every 10m
com.hermes.health-check.plist        — job staleness, every 10m
com.hermes.adhd-checkin.plist.disabled
com.hermes.crm-followups.plist.disabled
```

### Why launchd, not Hermes cron, for watchdogs
Hermes cron jobs have no OS sleep/wake integration. After a Mac wakes, the
network stack takes ~30–60s to recover. A cron-based watchdog will fire
immediately and see DNS failures, generating false alerts. launchd's
`StartInterval` timer is OS-managed and doesn't fire into a not-yet-awake
network in the same way.

**Rule:** Use launchd for wall-clock-sensitive jobs (briefing) and watchdogs
that depend on network availability. Use Hermes cron for jobs that can
tolerate a missed tick or that live entirely inside the Hermes/gateway process.

### Sleep/Wake False Alert Pattern (history)
From May 15, 2026 — when watchdogs ran as Hermes cron jobs — the Mac sleep/wake
cycle produced this pattern in `/tmp/litellm-launchd.log`:
```
03:02 - BedrockException - {"message":"Bedrock is unable to process your request."}
04:00 - BedrockException - {"message":"Too many connections, please wait before trying again."}
05:47 - BedrockException - Cannot connect to host bedrock-runtime.us-east-1.amazonaws.com:443
         ... nodename nor servname provided, or not known
```
All resolved by ~6:08 AM when the network fully recovered. These were NOT
real Bedrock failures — they were DNS resolution failures during network
stack re-initialization after sleep. Fixed by moving watchdogs to launchd.

### Diagnosing LiteLLM/Bedrock Errors
1. Check `/tmp/litellm-launchd.log` — look for actual error messages with timestamps
2. Check `~/.hermes/logs/errors.log` — MCP JSONRPC errors from `peekaboo` are harmless noise
3. Confirm LiteLLM is running: `ps aux | grep litellm`
4. Test live: `curl -fsS http://localhost:4000/health`
5. If `"Bedrock is unable to process"` — check if Mac just woke from sleep before assuming real failure
6. IAM credentials: `~/.aws/credentials [default]` has static keys. `aws sts get-caller-identity` may show SSO errors (irrelevant — proxy uses static IAM, not SSO).
