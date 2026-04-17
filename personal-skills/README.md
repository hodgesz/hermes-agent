# personal-skills

Fork-local skills that Hermes loads from outside `~/.hermes/skills/`. These are
versioned in the fork so they survive laptop failures — if this repo is cloned
fresh, the skills come back with it.

**Not upstream-bound.** These are personal; the `.github/` / upstream PR flow
should never include this directory.

## Setup on a fresh machine

Add this to `~/.hermes/config.yaml`:

```yaml
skills:
  external_dirs:
    - /absolute/path/to/hermes-agent/personal-skills
```

Hermes resolves `external_dirs` at scan time via
`agent.skill_utils.get_external_skills_dirs()`; relative paths are not
supported, so use the absolute checkout path (or a `~`-prefixed one).

## Skills

| Skill | Purpose |
|---|---|
| `adhd-founder-planner/` | `plan` / `migrate` / `dopamine` — break goals into 15-min steps with dopamine checkpoints |
| `personal-crm/` | Obsidian-vault-backed contact tracker (`People/*.md` with YAML frontmatter). Requires `HERMES_OBSIDIAN_VAULT` env var |
| `morning-briefing/` | Cron-ready daily briefing: wttr.in weather + news + markets + sports |

## Verify

```bash
python -c "from tools.skills_tool import _find_all_skills; [print(s['name']) for s in _find_all_skills() if s['name'] in ('adhd-founder-planner','personal-crm','morning-briefing')]"
```
