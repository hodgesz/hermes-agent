# Hermes Agent upgrade: 0.17.0 (v2026.6.19) → latest (upstream/main @ 190e1ffac)

Tracking doc for upgrading our fork from upstream `v2026.6.19` (0.17.0) to
`upstream/main` HEAD (`190e1ffac`).

## Status

- **Current installed base:** `v2026.6.19` (0.17.0) — `v0.17.0 (2026.6.19)`
- **Target:** `upstream/main` (`190e1ffac`, 976 commits past `v2026.6.19`)
- **Divergence:** fork is 30 commits ahead of `upstream/main`, 976 commits behind
- **Phase:** 0 in progress
- **Backup branch:** `pre-upgrade-20260819`
- **Worktree:** `../hermes-agent-upgrade` on branch `upgrade/latest`
- **Last updated:** 2026-08-19

## Overlapping File Surface (6 files)

- `cli.py`
- `gateway/run.py`
- `hermes_cli/config.py`
- `tools/browser_tool.py`
- `tools/mcp_tool.py`
- `tools/terminal_tool.py`

## New Features & Capabilities from Upstream

1. **Verification Stop Loop (`agent/verification_stop.py`)**: Enforces verification closure after landed file edits with bounded retries and config toggles (`agent.verify_on_stop`).
2. **Mixture of Agents (MoA)**: Selectable virtual models, presets in model picker, fallback restoration.
3. **Subagent Background Resume UX**: Visual status line & store for parked `delegate_task` background resume.
4. **Kanban Multi-Agent Board Dispatcher**: Worker + orchestrator plugin with typed block reasons.
5. **New Skills**:
   - `software-development/systematic-debugging` (tight feedback loop debugging)
   - `software-development/test-driven-development`
   - `software-development/hermes-agent-skill-authoring`
   - `devops/kanban-orchestrator` & `kanban-worker`
   - `apple/macos-computer-use`
   - `computer-use`
   - `email/himalaya`
   - `productivity/petdex`
   - `autonomous-ai-agents/hermes-agent`
6. **Messaging & Platform Updates**: Native WhatsApp media delivery via Baileys bridge, Feishu off-event-loop execution, Telegram improvements.
7. **Plugins**: OpenRouter image generation, memory plugin updates (Honcho, Mem0, Supermemory, Byterover, Hindsight).
8. **Desktop App Refinement**: Embed consent gates, inline rendering, zoom primitives, file editor.

## Phased Execution Plan

### Phase 0 — Pre-flight
- [ ] Commit pending working tree edits (`watchdog-litellm.sh` model update, `morning-briefing` skill notes).
- [ ] Record baseline verification (`hermes --version`, proxy health, `hermes -z`, watchdog, custom tests).
- [ ] Create backup branch `pre-upgrade-20260819`.
- [ ] Add worktree `../hermes-agent-upgrade` on `upgrade/latest`.

### Phase 1 — Rebase onto `upstream/main`
- [ ] Rebase fork commits onto `upstream/main`.
- [ ] Resolve any conflicts in the 6 overlapping files.
- [ ] Regenerate `uv.lock` via `uv lock`.

### Phase 2 — Worktree Verification
- [ ] Sync worktree dependencies: `uv sync --extra dev --extra messaging`.
- [ ] Run custom security tests: `test_mcp_approval.py`, `test_network_policy.py`, `test_docker_environment.py`.
- [ ] Run `ruff check .` and import smoke test.
- [ ] Run `hermes doctor` in worktree.

### Phase 3 — Live System Re-verification
- [ ] Migrate/update config if schema version bumped.
- [ ] Reset/fast-forward `main` to `upgrade/latest`.
- [ ] Sync live `.venv`: `uv sync --extra dev --extra messaging`.
- [ ] Verify proxy `/health`, `hermes -z "pong"`, watchdog, and Telegram briefing.
- [ ] Restart gateway if running.

### Phase 4 — Cleanup & Handoff
- [ ] Push to `origin/main` (with lease).
- [ ] Remove temporary worktree.
- [ ] Finalize documentation & update memory.
