# otto-workbench

Dotfiles and developer environment manager. Installs shell config, brew packages, git settings, Claude AI tooling, and editor config via a component framework.

## Stack

bash, zsh, bats-core (tests), brew (packages), jq/yq (YAML/JSON), shellcheck (lint)

## Commands

```bash
bats tests/                    # run all tests
bats tests/<file>.bats         # run one test suite
shellcheck <file>.sh           # lint a script
bin/local/validate-registries        # check registry YAML schema + cross-validation
bin/local/validate-components        # check component tier contracts
bin/local/validate-migrations        # check migration file conventions
bin/local/validate-skills            # check SKILL.md frontmatter conventions
bin/local/validate-cli-flags         # check CLI flag conventions (--repo, --pr/--branch exclusivity)
bin/local/generate-tool-context      # regenerate tools.generated.md from registries
git/bin/local/generate-git-rules     # regenerate git.generated.md from lib/conventions.sh
otto-workbench changelog       # show recent changes from conventional commits
```

## Conventions

- **Single source of truth** — every piece of data or config has exactly one authoritative owner. Display logic reads from the owner; it does not duplicate or re-derive the data. Runtime choices (e.g. Docker runtime) are recorded in state files (`~/.config/workbench/`); checks should read state, not infer from binary presence. When defaults must appear in multiple formats (YAML + shell), add a cross-validation test. Registry `*.registry.yml` files own tool documentation (`tools[]`). Registry `*.env.yml` files own env var declarations (`env[]`, `auth`), colocated with the consumer code that reads them. Env vars set programmatically at runtime (e.g. DOCKER_HOST) are NOT declared in registries.
- Dynamic discovery over hardcoded config — glob patterns, not individual entries. Test: "does adding a new item require editing this file?" If yes, use a convention-based alternative.
- Adding a brew tool = add to Brewfile + registry.yml. No other config edits needed. Env vars go in a `.env.yml` next to the consumer, not in the brew registry.
- Adding a migration = create `<component>/migrations/YYYYMMDD-slug.sh` with a `migration_YYYYMMDD_slug()` function. No registry edits needed. Migrations must not source `lib/ui.sh` or assign `WORKBENCH_DIR` — both are provided by the migration framework (`lib/migrations.sh`).
- Generated files (`tools.generated.md`, `git.generated.md`) are never edited directly — edit the source and regenerate.
- Config files in `zsh/config.d/` use `# duplicate-check: <pattern>` headers to prevent overlapping concerns.
- All scripts and git hooks use `#!/usr/bin/env bash` (not `#!/bin/bash`) to pick up Homebrew's modern bash on macOS. Bash 4.3+ is required. Never invoke scripts with `bash script.sh` — run them directly (`./script.sh` or `"$path/script.sh"`) so their shebang is honored.
- All scripts source `lib/ui.sh` via `git rev-parse --show-toplevel` — depth-independent, no `../` paths.
- Scripts use `set -e`.
- **Idempotency is required** — all setup scripts, sync functions, and migrations must be safe to re-run. Guard installs with presence checks, use `install_symlink` (not raw `ln`), and ensure repeated execution produces the same result with no side effects.
- **Return values via `local -n` (nameref), never `printf -v`** — `printf -v "$var"` silently writes to a same-named `local` in the current scope instead of the caller's variable. Use `local -n __out=$1` and assign `__out="value"`. The `__` prefix convention prevents collisions with caller variables.
- Migrations are state-tracked in `~/.config/workbench/migrations.applied` and auto-pruned when removed.
- **Documentation is part of the deliverable** — features, behavioral changes, and new tools must include doc updates before the PR is created. See `ai/guidelines/rules/general.md` (Comments & Documentation) for specifics.
- **PR descriptions use the repo template** — `.github/PULL_REQUEST_TEMPLATE.md` defines required sections (`## What`, `## Why`). Always structure PR bodies with these headers, whether creating via `task pr:create` or passing `--body` / `--body-file`. Never write freeform descriptions that omit the template sections.
- **Fix rules, not memories** — when a Claude behavior problem is identified in this repo, the fix is a rule change in `ai/guidelines/rules/` or `CLAUDE.md`, not a feedback memory. Rules in otto-workbench are the single source of truth for Claude behavior and apply globally via setup. Use memory only for things that genuinely don't belong in rules (user preferences, project context, references).

## claude-review Development

When adding or modifying a review phase, verify these integration points:
- `review_common.py`: `SEVERITIES` list, `SeverityConfig` fields (`posting`, `body_group`, `section`, `aliases`), `severity_by_key()`
- `review-orchestrate`: `_FINDING_SECTIONS` (derived from registry), `renumber_section()`, `merge_reviews()`, `build_prompt()` template rendering
- `review-post`: `renumber_for_posting()`, `parse_findings()` parser, `classify_findings()` posting routing
- `agents/reviewer.md`: output format (Phase 10 markdown template), finding ID patterns (`[M1]`, `[S1]`, etc.)
- `lib/review-templates/`: section headers referenced in synthesis and group templates

## Debugging claude-review

Review artifacts live in `~/.config/workbench/reviews/{repo}-{pr_or_branch}/`:

| File | Survives success | Purpose |
|------|-----------------|---------|
| `review.md` | yes | Final review output |
| `meta.json` | yes | PR metadata sidecar |
| `session.jsonl` | yes | Agent cost/usage/errors |
| `trail.jsonl` | yes | Structured trail log (decisions, spans, verification) |
| `prompt-stats.json` | yes | Prompt composition diagnostics |
| `prompt-*.md` | no (kept on failure) | Full prompts sent to agents |
| `pipeline.json` | no | Resume state for multi-phase |

**Diagnosing max-turns failures:**
1. Read `prompt-stats.json` — check `utilization_pct` and `file_contents.omitted` for prompt bloat
2. Read `session.jsonl` — count `tool_use` records to see how the agent spent its turns
3. Check `prompt-*.md` (preserved on failure) — look for oversized sections

**Diagnosing prompt bloat:**
- `prompt-stats.json` → `sections` shows per-section byte sizes
- `prompt-stats.json` → `file_contents.included` shows which files were injected and their sizes
- Large files with small diffs are automatically skipped by the density filter (`FILE_CONTENT_DENSITY_THRESHOLD`)
- Budget constants: `MAX_PROMPT_BYTES` (480KB), `TEMPLATE_OVERHEAD_BYTES` (20KB), `FILE_CONTENT_MIN_SIZE` (5KB)

## pr CLI Development

The `pr` script (`ai/claude/bin/pr`) is a thin dispatch layer. Each subcommand
delegates to an external script via `subprocess.run()`. JSON on stdout, status
messages on stderr.

### Delegation map

| Subcommand | Script | State updated by |
|------------|--------|------------------|
| `pr ci` | `ci-check` | script (updates state directly) |
| `pr ci --fix` | `ci-check --fix` | script (updates state directly) |
| `pr review` | `claude-review` | script (updates state directly) |
| `pr review --post` | `review-post` | `pr` wrapper |
| `pr review --repair` | `review-rebuild` (fallback) | `pr` wrapper |
| `pr review --summary` | none (local computation) | none |
| `pr comments` | `review-threads` | script (updates state directly) |
| `pr comments --triage` | `review-threads --triage` | script (updates state directly) |
| `pr comments --fix` | `review-threads --fix` | script (updates state directly) |
| `pr rebase` | `pr-rebase` | script (updates state directly) |
| `pr gc` | none (local via `review_gc`) | none |
| `pr fix` | `claude-review` (--fix), `ci-check` (--fix) | none |
| `pr status` | none (reads cached state) | none |

### Adding a new subcommand

1. Create the external script in `ai/claude/bin/`
2. Add argparse subparser in `pr`
3. Add `cmd_<name>` wrapper that delegates via `subprocess.run()`
4. If the subcommand has persistent state: add a dataclass to `pr_state.py`
   (serialized via generic `serde.to_dict()`/`serde.from_dict()`) and an `update_<name>()` function
5. Add `_render_<name>_section()` to `pr` for the `cmd_status` dashboard
6. Register in `ai/claude/registry.yml`
7. Add tests in `tests/`

### State management

- State file: `<worktree>/.workbench/state.json`
- Lib module: `ai/claude/lib/pr_state.py`
- Each domain has a dataclass (e.g., `CIDomain`, `RebaseSummary`) serialized
  via generic `serde.to_dict()`/`serde.from_dict()`
- Updated via `pr_state.update_<domain>(state, summary)` + `pr_state.save_state()`
- Scripts own their state updates — Python scripts import `pr_state` directly
- `pr_state.load_or_init()` provides DRY state loading across all scripts
- `pr_state.apply_state_update()` provides generic dict-based state updates
