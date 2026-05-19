---
name: morning-briefing
description: Warm daily briefing (weather + news + markets + sports) with tight anti-hallucination rules. Runs via launchd (com.hermes.morning-briefing), delivered to Telegram — under 500 words. See references/infrastructure.md for full stack topology and debugging guide.
version: 2.0.0
author: hodgesz
license: MIT
metadata:
  hermes:
    tags: [daily, briefing, launchd, morning, news, weather]
    config:
      - key: MORNING_BRIEFING_LOCATION
        description: City/zip for weather lookup (default Denver)
      - key: MORNING_BRIEFING_UNITS
        description: "imperial or metric (default imperial)"
prerequisites:
  commands: [python3]
---

# Morning Briefing

Produce a warm, accurate daily briefing for Jonathan. This runs under launchd
and is delivered verbatim to Telegram. Jonathan reads it on his phone first
thing — tone should feel like a friend who read the news for him.

## User Profile

- Name: Jonathan Hodges
- Location: Centennial, CO (Denver area)
- Timezone: Mountain Time
- Interests (in priority order):
  1. Pro cycling (Pogačar, Van der Poel, Vingegaard; Monuments, Grand Tours, Classics)
  2. Denver Nuggets (NBA)
  3. Auburn Tigers (CFB)
  4. Denver Broncos (NFL)
  5. Major tech/AI news
  6. Big world/US news
  7. Denver/Colorado local news

## Workflow

### 1. Gather local data (weather)

```bash
python3 "$SKILL_DIR/scripts/morning_data.py" --location "${MORNING_BRIEFING_LOCATION:-Denver}"
```

Parse the JSON for temperature, high/low, condition, precip chance.
If `weather.error` is present OR the script itself fails, fall back to
`web_search` for "Denver weather today forecast". **Never invent numbers.**

### 2. Top news (web_search, 3–4 items)

Call `web_search` for each category in priority order. **One search per
category** unless results are thin. Priorities:

1. One major world/US story
2. One tech/AI story
3. One cycling result or preview (if any race today/tomorrow)
4. One Denver/Colorado item (optional, only if notable)

**One sentence per headline. Always cite the source.** If a search returns
no relevant hits, skip the bullet — do not pad with training-data knowledge.

### 3. Markets (weekdays only)

Skip entirely on weekends. On weekdays, one `web_search` for
"S&P 500 today close" and report direction + approximate level + one notable
mover if visible in results. **Do not guess levels.** If results are stale
or unclear, say "Markets data unavailable — check later" instead of making
numbers up.

### 4. Sports (today/tomorrow only)

For each team in priority order (Nuggets, Auburn, Broncos, cycling),
`web_search` for games or results dated today or yesterday. Report:

- **Result** if the game is finished: "Nuggets 112, Thunder 108. Jokić 32/14/9."
- **Preview** if the game is today: "Nuggets host Timberwolves 1:30 PM MT — Game 1."
- **Skip the team entirely** if nothing is happening today/tomorrow.

Never report a score that wasn't in the search results. Never assume a
playoff matchup or date — search and confirm.

### 5. Today's focus (optional)

If the session history shows priorities, tasks, or meetings, include one
line. Otherwise skip.

## Output Format

Keep total under 500 words. Use Markdown (Telegram renders it). Emoji
are welcome — Jonathan likes a warm, friendly tone.

```text
Good morning, Jonathan! [Weekday, Month Day] ☀️

---

## 🌤️ Weather — Centennial, CO
[temp]°F, [condition]. High [X]° / Low [Y]°. [precip]% chance of rain.
[One short note about the day's weather if notable — cold front, wind,
 snow chance, nice afternoon, etc.]

---

## 🌍 News
- [Headline sentence] — *[Source]*
- [Headline sentence] — *[Source]*
- [Headline sentence] — *[Source]*

---

## 📈 Markets
S&P 500: [direction] at [level]. [Optional one-line driver.]

---

## 🏀 Sports
[Team section with emoji: 🏀 Nuggets, 🐯 Auburn, 🐴 Broncos, 🚴 Cycling.
 One or two sentences each. Skip any section with nothing to report.]

---

Have a great [weekday]! 🍺
```

## Hard Rules (Anti-Hallucination)

1. **Cite every factual claim.** Every headline and every score must come
   from a `web_search` result returned in this run. No training-data facts.
2. **No preamble in output.** Do not write "I'll search…", "Let me
   check…", "Good results on X". That commentary is TUI chrome — your
   final message is delivered verbatim to Telegram. Start directly with
   "Good morning, Jonathan!".
3. **Never fabricate scores, dates, or matchups.** If the search doesn't
   return a definitive result, skip the item or say "result pending".
4. **Weather from script first.** Only fall back to `web_search` if
   `morning_data.py` fails.
5. **Weekends: no markets section.**
6. **Skip rather than pad.** A short, accurate briefing is better than a
   long one with made-up filler.
7. **No investment advice.** Markets section is headline-level only.

## Silent Mode

If `HERMES_CRON_SILENT_IF_EMPTY=1` is set AND every section except weather
is empty, return the literal string `[SILENT]` so the delivery layer
suppresses the Telegram message.

## Known Pitfalls

- **Do not run briefings in a long-lived shared session.** If the same session accumulates 40+ messages, context compaction triggers. LiteLLM's translation of Anthropic tool call IDs (`tooluse_...` format) fails during compaction with HTTP 500 — killing the briefing mid-run.
- **Always use a cron job** so each morning briefing runs in its own fresh session. Manually requesting briefings in a persistent conversation causes the session to grow across days until compaction breaks it.
- If you see `⟳ compacting context…` in the failure message, the root cause is almost certainly a stale long-running session, not a model or API issue.
- **Watchdog false alerts after Mac sleep/wake (root cause: launchd vs Hermes cron).** When watchdogs run as Hermes cron jobs (every 10m), they have no awareness of Mac sleep/wake state. After wake, the network stack takes ~30–60s to recover — Bedrock DNS fails, producing `BedrockException: Bedrock is unable to process your request` or `Cannot connect to host … nodename nor servname provided`. These fire as real alerts before the network is up. **The correct fix is to run watchdogs as launchd agents** (using `StartInterval`), not Hermes cron — launchd's timer is OS-managed and respects sleep/wake cycles, so it won't fire blindly into a not-yet-awake network. See `references/infrastructure.md` for current topology.
- **"Bedrock is unable to process" ≠ Bedrock outage.** Before blaming AWS, check: (1) did the Mac just wake from sleep? (2) are the IAM static credentials in `~/.aws/credentials [default]` valid? The LiteLLM proxy uses static IAM keys — not SSO — so SSO expiry errors from `aws sts get-caller-identity` are irrelevant noise.
- **Hermes cron vs launchd for scheduled jobs.** Hermes cron is convenient but has no OS sleep/wake integration. For jobs that must fire reliably at a wall-clock time (briefing) or that must NOT fire during network-down windows (watchdogs), use launchd `StartCalendarInterval` / `StartInterval` instead. Hermes cron is best for jobs that can tolerate a missed tick or that live entirely inside the Hermes/gateway process. See `references/infrastructure.md` for the full stack.
