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
- Generated files (`tools.generated*.md`, `git.generated.md`) are never edited directly — edit the source and regenerate.
- Config files in `zsh/config.d/` use `# duplicate-check: <pattern>` headers to prevent overlapping concerns.
- **Idempotency is required** — all setup scripts, sync functions, and migrations must be safe to re-run. Guard installs with presence checks, use `install_symlink` (not raw `ln`), and ensure repeated execution produces the same result with no side effects.
- Migrations are state-tracked in `~/.config/workbench/migrations.applied` and auto-pruned when removed.
- **Documentation is part of the deliverable** — features, behavioral changes, and new tools must include doc updates before the PR is created. See `ai/guidelines/rules/general.md` (Comments & Documentation) for specifics.
- **PR descriptions use the repo template** — `.github/PULL_REQUEST_TEMPLATE.md` defines required sections (`## What`, `## Why`). Always structure PR bodies with these headers, whether creating via `task pr:create` or passing `--body` / `--body-file`. Never write freeform descriptions that omit the template sections.
- **Fix rules, not memories** — when a Claude behavior problem is identified in this repo, the fix is a rule change in `ai/guidelines/rules/` or `CLAUDE.md`, not a feedback memory. Rules in otto-workbench are the single source of truth for Claude behavior and apply globally via setup. Use memory only for things that genuinely don't belong in rules (user preferences, project context, references).
