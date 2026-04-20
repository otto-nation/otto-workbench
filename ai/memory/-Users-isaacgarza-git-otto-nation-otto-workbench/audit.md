---
name: Repo Audit — March 2026
description: Comprehensive resiliency, extensibility, and maintainability audit — 58 findings
type: project
---

Thorough audit completed 2026-03-23. 58 findings (R1-R15, E1-E11, M1-M23) — all addressed in Phases 1-5.
Second pass completed 2026-03-24. 23 additional findings (NEW-1 through NEW-23) in previously unaudited files.
**STATUS (2026-04-19): All 81 findings resolved. No open items.**

**Why:** User requested a deep audit to identify issues before continued feature work.
**How to apply:** Use this as a reference when working in any script — check if the file being touched has open findings before editing.

---

## RESILIENCY — High (10 findings)

| ID | File:Line | Issue |
|----|-----------|-------|
| R1 | `ai/setup.sh:98` | `"register_${_tool}_steps"` called without checking the function exists — silent failure, no diagnostic |
| R2 | `docker/setup.sh:64` | `. "$SCRIPT_DIR/$DOCKER_RUNTIME/setup.sh"` has no file-existence guard — missing file is silent no-op |
| R3 | `task/steps.sh:35` | `curl ... | sh` pipes remote script to shell without SHA verification, no timeout, follows redirects |
| R4 | `brew/setup.sh:16` | stderr discarded on `brew info --json=v2`; malformed JSON undetected, all jq calls silently empty |
| R5 | `ai/claude/steps.sh:27` | `tmp=$(mktemp)` with no `trap 'rm -f "$tmp"' RETURN` — leaks temp file on jq failure |
| R6 | `ai/claude/steps.sh:188` | Subshell failure in `$()` not caught by `set -e` — `$result` can be silently unset |
| R7 | `install.sh:197` | `bash -c "$check_cmd"` — unvalidated string from setup.conf executed directly; injection risk |
| R8 | `docker/colima/setup.sh:18` | Symlinks Colima socket without checking it exists — creates dangling symlink if Colima never started |
| R9 | `lib/ui.sh:304` | `symlink_dir` glob expansion proceeds even if `$item` is a broken symlink |
| R10 | `docker/summary.sh:14` | `readlink` returns relative path; subsequent path-prefix match assumes absolute — wrong result silently |

## RESILIENCY — Medium (5 findings)

| ID | File:Line | Issue |
|----|-----------|-------|
| R11 | `task/steps.sh:22` | `read -n 1` with no `[[ -t 0 ]]` guard — hangs in CI / non-interactive shells |
| R12 | `brew/setup.sh:58` | Regex requires quoted package names; unquoted entries (valid Brewfile syntax) silently fall through |
| R13 | `docker/summary.sh:18` | "If not Colima, assume OrbStack" — will misidentify Docker Desktop, Podman, nerdctl |
| R14 | `lib/constants.sh:15` | `WORKBENCH_DIR` derived without `readlink -f` — wrong if `lib/constants.sh` itself is symlinked |
| R15 | `ai/setup.sh:75` | After "no tools selected", `run_steps` still called with empty array — should early-return |

---

## EXTENSIBILITY — High Friction (7 findings)

| ID | Location | Issue |
|----|----------|-------|
| E1 | `install.sh:242–247` | Core component sequence hardcoded by name — adding a core component requires editing install.sh |
| E2 | `install.sh:242–247` | No ordering enforcement for core components — wrong order/duplicates have no detection |
| E3 | `zsh/steps.sh:60` | `loader.zsh` load order manually maintained; no mechanism detects when layer dir is added but loader is stale |
| E4 | `bin/validate-registries` + `bin/generate-tool-context` | Both hardcode same registry schema — adding new validation type requires editing both |
| E5 | `ai/setup.sh:98` | Requires `register_<name>_steps` but doesn't validate it exists — tool with only `sync_<name>` silently fails |
| E6 | `docker/setup.sh:27` | Runtime discovery is filename-only — no metadata, no way to describe a runtime without implementing it |
| E7 | `install.sh:97–138` | Component metadata split across `setup.conf` AND `install.components` — adding a component requires editing both |

## EXTENSIBILITY — Medium Friction (4 findings)

| ID | Location | Issue |
|----|----------|-------|
| E8 | `lib/ui.sh` | No `NO_COLOR` env var support — ANSI codes always emitted, breaks piping |
| E9 | `bin/generate-tool-context:104` | Filter type (`_is_installed`) hardcoded — adding new filter requires editing script |
| E10 | `docker/setup.sh:21–48` | `select_runtime` reimplements menu selection instead of calling `select_menu` from lib/ui.sh |
| E11 | `bin/validate-registries:69–70` | Reimplements field extraction inline with sed — `conf_get()` already exists in install.sh but not shared |

---

## MAINTAINABILITY — Duplication (4 findings)

| ID | Files | Issue |
|----|-------|-------|
| M1 | `ai/claude/steps.sh` vs `ai/kiro/steps.sh` | ~90% duplicate — install, agents, rules, sync, summary — only MCPs/skills differ |
| M2 | `brew/setup.sh:143–185` | `_brew_select_category` and `_brew_select_optional` both collect Brewfiles with duplicated array patterns |
| M3 | `docker/setup.sh:21–48` | `select_runtime` duplicates `select_menu` logic from lib/ui.sh |
| M4 | `bin/validate-components:69–70` | Reimplements `conf_get()` with inline sed — function already exists in install.sh |

## MAINTAINABILITY — Function Size / Complexity (3 findings)

| ID | File:Line | Issue |
|----|-----------|-------|
| M5 | `lib/ui.sh:143–219` | `select_menu` is 77 lines — handles prompt formatting, range validation, and defaults; should split |
| M6 | `install.sh:126–182` | `discover_components` + `select_components` share registry iteration; could unify |
| M7 | `ai/claude/steps.sh:124–138` | `_print_item_list` mixes directory iteration, basename extraction, and color formatting |

## MAINTAINABILITY — Inconsistency (3 findings)

| ID | Finding |
|----|---------|
| M8 | Color usage inconsistent — some info paths use `CYAN`, others `BLUE`; no documented color semantics |
| M9 | Error recovery strategy inconsistent: `install.sh:200` warns+continues; `ai/setup.sh:98` hard-fails |
| M10 | Two separate component systems: core (auto-sourced `*/steps.sh`) vs optional (`install.components`) — different rules, no unified mental model |

## MAINTAINABILITY — Test Coverage Gaps (7 findings)

| ID | What's Not Tested |
|----|-------------------|
| M11 | Interactive prompts — `confirm`, `confirm_n`, `select_menu` have zero bats tests |
| M12 | Idempotency — no test verifies running install twice leaves identical state |
| M13 | Error paths — missing dirs, invalid JSON, network failures not covered |
| M14 | `zsh/steps.sh` layer discovery and loader.zsh sync validation |
| M15 | `docker/` runtime selection and socket setup |
| M16 | Core install flow (install.sh end-to-end) |
| M17 | `symlink_dir --prune` functionality |

---

## SECOND PASS — NEW-1 through NEW-23 (2026-03-24)

Previously unaudited files: `bin/aliases`, `bin/cleanup-testcontainers`, `bin/get-secret`, `bin/mem-analyze`, `bin/otto-workbench`, `bin/task`, `bin/claude-init`, `bin/claude-rules`, `git/.gitconfig`, `iterm/steps.sh`, `Taskfile.global.yml`, `zsh/config.d/loader.zsh`, `zsh/config.d/aliases/docker.zsh`, `tests/install_components.bats`

| ID | Cat | File:Line | Issue |
|----|-----|-----------|-------|
| NEW-1 | R | `bin/aliases:11` | `ZSH_CONFIG_DIR` not validated before use — empty var glob hits root filesystem |
| NEW-2 | R | `bin/aliases:68-69` | `local` used outside a function — top-level loop variable, misleads readers |
| NEW-3 | R | `bin/cleanup-testcontainers:56-61` | `grep -c .` on empty string returns 1 not 0; `docker rm -f ""` errors |
| NEW-4 | R | `bin/get-secret:46-47` | Unquoted `$PROFILE_ARG` breaks on AWS profile names with spaces |
| NEW-5 | R | `bin/get-secret:65-66` | Word-splitting on `--output text` breaks secret names containing spaces |
| NEW-6 | R | `bin/mem-analyze:85` | `head+1/tail` ps header removal is wrong — numeric sort puts header last, not first |
| NEW-7 | M | `bin/mem-analyze:88-135` | App name extraction duplicated verbatim in two pipeline sections |
| NEW-8 | R | `bin/otto-workbench:52-56` | No guard against duplicate `sync_<name>()` across steps.sh files; second silently wins |
| NEW-9 | R | `bin/task:21` | `grep -v "^$LOCAL_BIN_DIR$"` uses regex — `.` in path matches unintended entries; fix: `grep -Fxv` |
| NEW-10 | R | `bin/task:46` | Taskfile detection misses lowercase `taskfile.yml`/`taskfile.yaml` variants |
| NEW-11 | E | `bin/claude-init:22` | `WORKBENCH_DIR` re-derived locally instead of relying on `lib/constants.sh` export |
| NEW-12 | E | `bin/claude-init:38-64` | Stack detection only checks cwd; misses monorepos with nested build files |
| NEW-13 | M | `bin/claude-rules:139-167` | `workbench.md` generation could clobber a workbench-sourced symlink if repo gains a matching file |
| NEW-14 | R | `git/.gitconfig:23` | `fixup` alias stashes but never pops on rebase failure — silent data loss |
| NEW-15 | R | `git/.gitconfig:27-28` | `deploy-branch`/`deploy-commit` use `--no-verify` — bypasses gitleaks on push |
| NEW-16 | M | `git/.gitconfig:26` | `rebase-off-main` stashes unconditionally, never pops — accumulates phantom stash entries |
| NEW-17 | R | `iterm/steps.sh:41` | `open` returns immediately; `success` printed before iTerm2 processes the theme file |
| NEW-18 | R | `Taskfile.global.yml:73` | No empty-message guard before `git commit -m "$AI_MSG"` |
| NEW-19 | R | `Taskfile.global.yml:104-116` | `trap cleanup EXIT` registered after `mktemp` calls — disk-full mid-way leaks temp files |
| NEW-20 | M | `Taskfile.global.yml:262-264` | `{{.TASKFILE_DIR}}` path strip may mismatch when Taskfile is symlinked |
| NEW-21 | E | `zsh/config.d/loader.zsh:23-24` | New zsh layers require manually editing `loader.zsh` — inconsistent with auto-discovery everywhere else |
| NEW-22 | R | `zsh/config.d/aliases/docker.zsh:19` | `DOCKER_HOST` uses `COLIMA_PROFILE` var, diverges from canonical socket symlink in `docker/steps.sh` |
| NEW-23 | M | `tests/install_components.bats:63` | Orphan check fails silently — no output identifying which dir is the orphan |

## MAINTAINABILITY — Clarity / Documentation (6 findings)

| ID | File:Line | Issue |
|----|-----------|-------|
| M18 | `lib/ui.sh` top | `SYMLINK_MODE=no-prompt` checked internally but never documented — callers must read source |
| M19 | repo-wide | No `DEVELOPER.md` documenting the `sync_<name>()` contract |
| M20 | `git/steps.sh:92–93` | Hooks symlinked without checking source files exist — dangling if hook files missing |
| M21 | `docker/summary.sh:14` | `readlink` without `-f` — intent is absolute path matching but relative path may be returned |
| M22 | generated files | No checksum/hash comment in `tools.generated.md` / `git.generated.md` — silent corruption undetectable |
| M23 | `install.sh:57` | Literal `$HOME` grep pattern is correct but deeply confusing without an explanatory comment |
