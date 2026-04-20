---
name: Audit Fix Plan — Second Pass (March 2026)
description: Prioritized plan for 23 new findings from the second audit pass
type: project
---

Fixes for NEW-1 through NEW-23 in 4 phases. Each phase is independently committable.

**Why:** Second audit pass found real bugs in bin/ scripts, gitconfig aliases, and Taskfile tasks that were not covered in the first pass.
**Status:** All phases A–D complete. NEW-8, NEW-21 complete. E1/E2 architectural redesign complete. M12 idempotency tests complete (131/131 pass).

---

## Phase A — Real Bugs (High Impact Resiliency) ✅

*Commit: `fix(bin): fix grep-c empty string bug, ps header sort, task PATH regex`*

- **NEW-3** `bin/cleanup-testcontainers:56-61` — `grep -c .` on empty string returns 1; guard with `[[ -z "$CONTAINERS" ]]`
- **NEW-6** `bin/mem-analyze:85` — use `ps aux | tail -n +2 | sort -nrk 4 | head -$TOP_PROCESSES_COUNT` to correctly strip header
- **NEW-9** `bin/task:21` — change `grep -v "^$LOCAL_BIN_DIR$"` to `grep -Fxv "$LOCAL_BIN_DIR"` (fixed-string, no regex)
- **NEW-10** `bin/task:46` — add `taskfile.yml`/`taskfile.yaml` lowercase variants to detection condition
- **NEW-19** `Taskfile.global.yml:104-116` — move `trap cleanup EXIT` before first `mktemp` call
- **NEW-18** `Taskfile.global.yml:73` — add `[[ -n "$AI_MSG" ]] || { echo "✗ Empty commit message — aborting"; exit 1; }` before commit

---

## Phase B — Data Loss / Security ✅

*Commit: `fix(git): fix fixup stash leak, remove --no-verify from deploy aliases`*

- **NEW-14** `git/.gitconfig:23` — `fixup` alias: add `|| git stash pop` to the rebase command so stash is popped on failure
- **NEW-15** `git/.gitconfig:27-28` — remove `--no-verify` from `deploy-branch` and `deploy-commit`
- **NEW-16** `git/.gitconfig:26` — `rebase-off-main`: wrap stash in a condition (only stash if `git status --porcelain` is non-empty), add stash pop at end

---

## Phase C — Runtime Correctness ✅

*Commit: `fix(docker,bin): fix DOCKER_HOST socket path, AWS profile arg, get-secret word split`*

- **NEW-22** `zsh/config.d/aliases/docker.zsh:19` — replace `~/.colima/$COLIMA_PROFILE/docker.sock` with `$DOCKER_RUN_DIR/docker.sock` (the canonical symlink)
- **NEW-4** `bin/get-secret:46-47` — switch from string `PROFILE_ARG` to array `PROFILE_ARGS=(--profile "$AWS_PROFILE")`
- **NEW-5** `bin/get-secret:65-66` — switch from `--output text` word-splitting to `--output json | jq -r '.SecretList[].Name'` + `mapfile`
- **NEW-1** `bin/aliases:11` — add `[[ -n "$ZSH_CONFIG_DIR" ]] || { echo "ZSH_CONFIG_DIR not set" >&2; exit 1; }` guard

---

## Phase D — Maintainability & Test Quality ✅

*Commit: `refactor(bin,tests): extract app_name helper, improve orphan test error message`*

- **NEW-7** `bin/mem-analyze:88-135` — extract `_app_name()` helper, replace two identical blocks
- **NEW-23** `tests/install_components.bats:63` — collect orphans into array, report all at once with names
- **NEW-11** `bin/claude-init:22` — remove redundant local `WORKBENCH_DIR` derivation; rely on `lib/constants.sh`

---

## Deferred / Won't Fix

| ID | Rationale |
|----|-----------|
| NEW-2 | `local` outside a function in zsh is benign; fixing it adds noise without benefit |
| NEW-8 | ✅ DONE — added duplicate sync_<name>() detection to bin/validate-components using sort/uniq-d (bash 3.2 compatible) |
| NEW-12 | Monorepo detection in claude-init is a feature request, not a bug |
| NEW-13 | workbench.md collision requires workbench repo to gain an identically-named file — theoretical |
| NEW-17 | iterm `open` async behavior is a UX issue, not a correctness issue |
| NEW-20 | Taskfile.global.yml TASKFILE_DIR path strip is a cosmetic UX issue |
| NEW-21 | ✅ DONE — added comment to loader.zsh explaining why load order is manual (order matters; auto-discovery would break guarantees) |

---

## Architectural Work (E1/E2) ✅

After sequential thinking analysis (9 design patterns compared), implemented:

1. **install.sh auto-discovery** — replaced 5 hardcoded `sync_*` calls with glob `*/steps.sh`, skip if sibling `setup.conf` (= core components). `step_task_install` kept explicit as a preflight check.
2. **Dependency framework** — `depends = brew` field in `setup.conf`; `select_components()` resolves deps (iterate-until-stable) and re-sorts by COMPONENT_DIRS order for menu display.
3. **`docker/setup.conf`, `iterm/setup.conf`, `ai/setup.conf`** — all got `depends = brew`.
4. **`bin/validate-components`** — added dependency ordering validation section.
5. **`zsh/steps.sh`** — folded `step_zshrc` into `sync_zsh()` (gap: sync never repaired .zshrc).
6. **`CONTRIBUTING.md`** — added "How discovery works" section.

## M12 Idempotency Tests ✅

`tests/sync_idempotency.bats` — 10 tests covering sync_bin, sync_git, sync_zsh. Each runs the sync function twice in a tmpdir (HOME overridden) and asserts identical filesystem state. Full suite: 131/131.
