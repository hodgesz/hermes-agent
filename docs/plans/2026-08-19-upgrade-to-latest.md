# Hermes Agent upgrade: 0.17.0 (v2026.6.19) → latest (upstream/main @ 190e1ffac)

Tracking doc for upgrading our fork from upstream `v2026.6.19` (0.17.0) to
`upstream/main` HEAD (`190e1ffac`).

## Status

- **Current installed base:** `v2026.8.18` (0.20.4 tag) — `v0.20.4 (2026.8.18)`
- **Target tag:** `tags/v2026.8.18` (release tag from 2026-08-18)
- **Status:** **UPGRADE COMPLETE**
- **Backup branch:** `pre-upgrade-20260819`
- **Last updated:** 2026-08-19

## Overlapping File Surface (6 files)

- `cli.py`
- `gateway/run.py`
- `hermes_cli/config.py`
- `tools/browser_tool.py`
- `tools/mcp_tool.py`
- `tools/terminal_tool.py`

## New Features & Capabilities from Upstream

1. **Loop Capability & Verification Stop (`agent/verification_stop.py`)**: Enforces verification closure after landed file edits with bounded retries and config toggles (`agent.verify_on_stop`).
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
- [x] Commit pending working tree edits (`watchdog-litellm.sh` model update, `morning-briefing` skill notes).
- [x] Record baseline verification (`hermes --version`, proxy health, `hermes -z`, watchdog, custom tests).
- [x] Create backup branch `pre-upgrade-20260819`.
- [x] Add worktree `../hermes-agent-upgrade` on `upgrade/latest`.

### Phase 1 — Rebase onto `tags/v2026.8.18`
- [x] Rebase 31 fork commits onto `tags/v2026.8.18`.
- [x] Resolve conflicts in `hermes_cli/config_defaults.py`, `tools/web_tools.py`, `tools/environments/docker.py`, `tools/terminal_tool.py`, and `tests/tools/test_docker_environment.py`.
- [x] Regenerate `uv.lock` via `uv lock`.

### Phase 2 — Worktree Verification
- [x] Sync worktree dependencies: `uv sync --extra dev --extra messaging`.
- [x] Run custom security tests: `test_mcp_approval.py`, `test_network_policy.py`, `test_docker_environment.py` (105/105 passed).
- [x] Run `ruff check .` and import smoke test.
- [x] Run `hermes doctor` in worktree.

### Phase 3 — Live System Re-verification
- [x] Migrate config schema version (v30 → v37).
- [x] Reset `main` to `upgrade/latest`.
- [x] Sync live `.venv`: `uv sync --extra dev --extra messaging`.
- [x] Verify `hermes -z "pong"` (output: `pong`), watchdog (`watchdog-litellm.sh`), and `hermes doctor --fix`.
- [x] Restart messaging gateway daemon (`PID 91780` running v0.20.4).

### Phase 4 — Cleanup & Handoff
- [x] Push to `origin/main` (with lease).
- [x] Remove temporary worktree.
- [x] Finalize documentation & update memory.
