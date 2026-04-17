---
name: morning-briefing
description: Concise daily morning briefing (weather + news + markets + sports). Designed for cron invocation and Telegram delivery — keeps output under 500 words.
version: 1.0.0
author: hodgesz
license: MIT
metadata:
  hermes:
    tags: [daily, briefing, cron, morning, news, weather]
    config:
      - key: MORNING_BRIEFING_LOCATION
        description: City/zip for weather lookup (default Denver)
      - key: MORNING_BRIEFING_UNITS
        description: "imperial or metric (default imperial)"
prerequisites:
  commands: [python3]
---

# Morning Briefing

Generate a concise daily morning briefing. Use this skill when the user asks for a morning briefing, daily summary, or when triggered by the scheduled cron job.

## Invocation

- **Cron** (see Phase 6 of the fork plan): `0 7 * * * --skill morning-briefing --deliver telegram`
- **Manual**: user asks "morning briefing" or `/morning-briefing`

## User Profile

- Name: Jonathan Hodges
- Location: Centennial, CO (Denver area)
- Timezone: Mountain Time
- Interests: Pro cycling (Pogačar, Van der Poel, etc.), Broncos, Nuggets, Auburn Tigers, tech/AI, local events, big news

## Workflow

### 1. Gather local data

Run the gather script first. It returns weather + date + market-hours hint as JSON (no model calls, stdlib only, cheap):

```bash
python3 "$SKILL_DIR/scripts/morning_data.py" --location "${MORNING_BRIEFING_LOCATION:-Denver}"
```

Parse the JSON. If `weather.error` is present, fall back to `web_search` for "Denver weather today".

### 2. Top news (web_search)

Use `web_search` for 3–4 top headlines. Prioritize in this order:

1. Major world/US news
2. Tech/AI news
3. Pro cycling results or major stage races
4. Denver/Colorado local news worth noting

**One sentence per headline.** Include source name. Deduplicate — if three sources run the same story, it's one bullet.

### 3. Markets (weekdays only)

If `market_hint.likely_open` is true OR it's a weekday morning pre-open, search "stock market today S&P 500". Report:

- S&P 500 direction + approximate level
- One notable mover if any
- Skip entirely on weekends

### 4. Sports (if notable)

Check for recent results or upcoming games for: Broncos, Nuggets, Auburn Tigers, pro cycling races. Skip the section if nothing notable (don't pad).

### 5. Today's focus (optional)

If the user has mentioned priorities, tasks, or meetings in recent conversations, include a one-line reminder. Otherwise skip.

## Output Format

Keep the total under 500 words — this goes to Telegram.

```text
Morning Briefing — [Day, Month Date]

WEATHER (Denver)
[temp]°F, [condition]. High [X]° / Low [Y]°. [precip]% chance of rain.

NEWS
- [Headline 1] — [Source]
- [Headline 2] — [Source]
- [Headline 3] — [Source]

MARKETS
S&P 500: [direction] at [level]. [Notable mover if any.]

SPORTS
[Any notable results or upcoming games]

Have a great day!
```

## Cron Silent Mode

If the cron runner sets `HERMES_CRON_SILENT_IF_EMPTY=1` and every section other than weather is empty (weekend + no news + no sports), return the literal string `[SILENT]` so the cron delivery layer suppresses the Telegram message.

## Rules

1. **Brevity wins**: 500 words max, short bullets, no preamble like "Here's your briefing"
2. Don't fabricate headlines — if `web_search` fails, skip the section and note it briefly
3. Use `morning_data.py` weather first; only fall back to `web_search` if it errors
4. No emoji unless the user explicitly asks — per project convention
5. Never include investment advice; markets section is headline-level only
