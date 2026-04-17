---
name: personal-crm
description: Obsidian-backed personal CRM. Track contacts, last-touch dates, follow-ups, and relationship notes stored as markdown files in an Obsidian vault's People/ folder.
version: 1.0.0
author: hodgesz
license: MIT
metadata:
  hermes:
    tags: [crm, contacts, relationships, obsidian, followups]
    config:
      - key: HERMES_OBSIDIAN_VAULT
        description: Absolute path to the Obsidian vault root (script reads People/ inside it)
prerequisites:
  commands: [python3]
---

# Personal CRM (Obsidian-Backed)

A local-first personal CRM. Contacts are plain markdown files in an Obsidian vault's `People/` folder, so every note is browsable in Obsidian itself. The skill's script (`scripts/gather_contacts.py`) reads YAML frontmatter plus dated log entries to surface follow-ups, stale relationships, and quick lookups.

## Data Model

One markdown file per contact at `<vault>/People/<name>.md`:

```markdown
---
name: Jane Smith
company: Example Corp
role: CTO
email: jane@example.com
tags: [investor, advisor]
last_contact: 2026-03-12
follow_up: 2026-04-25
follow_up_note: Send updated deck
---

# Jane Smith

Introduced via Alex at the SF dinner.

- 2026-03-12: Met at Example HQ, discussed Series B thesis
- 2026-02-18: First intro call, 30 min
```

Dated bullets (`- YYYY-MM-DD: …`) are auto-parsed as log entries.

## Required Setup

Set the vault path once in your shell profile or via `hermes config set-env`:

```bash
export HERMES_OBSIDIAN_VAULT="/absolute/path/to/MyVault"
```

Then ensure `<vault>/People/` exists. The script never writes to the vault; contact edits happen through conversation → the agent uses `file_write` / `file_edit` tools.

## Commands

All commands return JSON so the agent can compose follow-up actions.

```bash
python3 "$SKILL_DIR/scripts/gather_contacts.py" list
python3 "$SKILL_DIR/scripts/gather_contacts.py" search "conference"
python3 "$SKILL_DIR/scripts/gather_contacts.py" show "Jane Smith"
python3 "$SKILL_DIR/scripts/gather_contacts.py" followups --by today   # or: week | month
python3 "$SKILL_DIR/scripts/gather_contacts.py" stale --days 90
```

## When to Use

- User mentions a person by name and wants context ("who is Jane Smith?")
- User asks "who should I follow up with this week?"
- User wants to log a new interaction ("add a note that I met Jane today")
- User asks "who haven't I talked to in a while?"

## Workflow

### Lookup

1. Run `show <name>` — if multiple matches, show the list and ask which
2. Summarize: company/role, last contact date, recent log entries, pending follow-up

### Add a contact

1. Ask name (required), company, role, email, tags
2. Write `<vault>/People/<slugified-name>.md` with the frontmatter template above
3. Set `last_contact` to today
4. Ask if there's a follow-up needed; if yes, set `follow_up` and `follow_up_note`

### Log a new touch

1. Open the existing contact file
2. Update frontmatter `last_contact` to today
3. Append a new dated bullet under the notes section
4. If a follow-up was agreed, update `follow_up` / `follow_up_note`

### Follow-up digest

1. `followups --by week` for weekly review, `--by today` for daily
2. Present sorted by most-overdue first; include the note verbatim so the user knows why it was flagged

### Stale relationships

1. `stale --days 90` (default) for a quarterly check-in prompt
2. Group by tag when presenting to the user (investors, advisors, friends) if there are many

## Rules

1. Always confirm before writing to a contact file; this is personal data
2. Never expose the full JSON output to the user — summarize in prose
3. When the vault path is missing, say so directly and ask the user to set `$HERMES_OBSIDIAN_VAULT`
4. Dates are ISO (`YYYY-MM-DD`) — never use "tomorrow" or relative strings in frontmatter
5. Slugify names conservatively for filenames: lowercase, spaces → dashes, strip diacritics (`Günter → gunter`)
