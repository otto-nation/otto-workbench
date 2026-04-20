---
name: Audit Fix Plan — March 2026
description: Prioritized implementation plan for the 58-finding March 2026 audit, organized into 5 phases
type: project
---

Fixing all 58 audit findings in 5 independently-committable phases.

**Why:** March 2026 audit identified critical resiliency gaps (injection risk, temp file leaks, dangling symlinks), high-friction extensibility pain points, and significant duplication in AI tool setup code.
**How to apply:** Work through phases in order. Each phase is independently committable and leaves the repo in a shippable state.

---

## Phase 1 — Resiliency: Security & Safety (IDs: R3, R7, R2, R5, R14)
*Commit: `fix(security): harden remote installs, command injection, and temp file leaks`*

- **R3** `task/steps.sh:35` — Pin taskfile install to a specific version; add `--max-time 30` to curl; verify SHA or use brew instead
- **R7** `install.sh:197` — Validate `check_cmd` from setup.conf; restrict allowed characters or document the trust model
- **R2** `docker/setup.sh:64` — Add `[[ -f "$SCRIPT_DIR/$DOCKER_RUNTIME/setup.sh" ]] || { err "..."; exit 1; }` before sourcing
- **R5** `ai/claude/steps.sh:27` — Add `trap 'rm -f "$tmp"' RETURN` after every `mktemp`
- **R14** `lib/constants.sh:15` — Add `readlink -f` to WORKBENCH_DIR derivation to handle symlinked lib/

---

## Phase 2 — Resiliency: Runtime Guards (IDs: R1, R4, R6, R8, R10, R11, R13, R15)
*Commit: `fix(resilience): add missing guards, fix silent failures, fix non-interactive hangs`*

- **R1** `ai/setup.sh:98` — Check `declare -f "register_${_tool}_steps" > /dev/null` before calling
- **R4** `brew/setup.sh:16` — Validate `brew info` JSON output is non-empty before piping to jq
- **R6** `ai/claude/steps.sh:188` — Explicit `|| { err "..."; return 1; }` after subshell assignment
- **R8** `docker/colima/setup.sh:18` — Check socket exists before symlinking; warn if not
- **R10** `docker/summary.sh:14` — Use `readlink -f` or resolve relative path to absolute before matching
- **R11** `task/steps.sh:22` — Add `[[ -t 0 ]] ||` guard before `read -n 1`
- **R13** `docker/summary.sh:18` — Explicit `[[ "$socket_target" == *"orbstack"* ]]` instead of catch-all else
- **R15** `ai/setup.sh:75` — Early return after "no tools selected" instead of continuing to `run_steps`

---

## Phase 3 — Extensibility: Shared Library & DRY (IDs: E10, E11, M3, M4, E8)
*Commit: `refactor(lib): extract conf_get to lib, unify select_runtime, add NO_COLOR support`*

- **E10 + M3** `docker/setup.sh:21–48` — Replace `select_runtime` with a call to `select_menu` from lib/ui.sh
- **E11 + M4** `bin/validate-components` and `bin/validate-registries` — Move `conf_get()` into `lib/ui.sh` (or a new `lib/conf.sh`); replace all inline sed implementations
- **E8** `lib/ui.sh` — Add `NO_COLOR` support: `[[ -t 1 && -z "${NO_COLOR:-}" ]]` guard in all color output functions

---

## Phase 4 — Maintainability: Reduce Duplication (IDs: M1, M2, M8, M9, M19)
*Commit: `refactor(ai): extract shared AI setup helpers; document component contract`*

- **M1** `ai/claude/steps.sh` vs `ai/kiro/steps.sh` — Extract shared logic into `lib/ai-setup.sh`: `step_install_tool`, `step_sync_agents`, `step_sync_rules`, `step_sync_rules_dir`; each tool's steps.sh becomes a thin config+delegation layer
- **M2** `brew/setup.sh:143–185` — Extract Brewfile collection into `_brew_collect_files DIR` helper
- **M8** `lib/ui.sh` — Add color semantics comment at top: RED=error, YELLOW=warn, GREEN=success, BLUE=info, CYAN=section/label, DIM=metadata
- **M9** Document error recovery strategy: component failures warn+continue; function contract violations hard-fail
- **M19** Create `DEVELOPER.md` documenting: sync_<name>() contract, SYMLINK_MODE env var, component registration steps, color conventions

---

## Phase 5 — Test Coverage (IDs: M11–M17)
*Commit: `test: add coverage for interactive prompts, idempotency, error paths`*

- **M11** `tests/ui.bats` — Test `confirm`, `confirm_n`, `select_menu` with stdin mocking
- **M12** Idempotency test — Run `sync_bin`, `sync_git`, `sync_zsh` twice in tmpdir; verify identical state
- **M13** Error path tests — Missing dirs, invalid JSON, missing executables
- **M14** `tests/zsh_steps.bats` — Layer discovery and loader.zsh sync detection
- **M15** `tests/docker_setup.bats` — Runtime selection with mocked `select_menu`
- **M16** Integration test for install.sh core flow (using SYMLINK_MODE=no-prompt, tmpdir)
- **M17** `symlink_dir --prune` test — verify it removes stale symlinks only

---

## Deferred / Won't Fix (with rationale)

| ID | Rationale |
|----|-----------|
| E1–E2 | Core component hardcoding is an architectural change; benefit unclear without more components |
| E3 | zsh loader order is intentional; auto-detection would require runtime validation |
| E4 | Registry schema coupling is acceptable; scripts are small and easy to update together |
| E6 | Docker runtime metadata via setup.conf is a nice-to-have, not blocking |
| E7 | Two-file component registration is a known trade-off; changing it would require large refactor |
| M5 | select_menu complexity is contained; splitting adds indirection without clear gain |
| M6 | discover + select_components overlap is minimal; refactor adds risk |
| M7 | _print_item_list is 15 lines; splitting adds complexity |
| R9 | lib/ui.sh broken-symlink guard is defense-in-depth; existing check at :301 is sufficient |
| M10 | Two-system architecture is the biggest long-term concern but requires full redesign |
| M22–M23 | Generated file checksums and comment clarity are low-impact |
