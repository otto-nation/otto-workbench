# otto-workbench

Dotfiles and developer environment manager. Installs shell config, brew packages, git settings, Claude AI tooling, and editor config via a component framework.

## Stack

bash, zsh, python (the `ai/` subsystem — `ai/lib/` and most of `ai/claude/bin/`), bats-core + pytest (tests), brew (packages), jq/yq (YAML/JSON), shellcheck (lint)

## Commands

```bash
bats tests/                    # run all bats tests
bats tests/<file>.bats         # run one bats suite
pytest tests/                  # run all Python tests
pytest tests/<file>.py         # run one Python suite
shellcheck <file>.sh           # lint a script
bin/local/validate-all               # run every validator
bin/validate-* / bin/local/validate-*  # the individual validators validate-all discovers
bin/local/generate-tool-context      # regenerate tools.generated.md from registries
bin/local/compose-docs               # recompose docs/*.md from docs/*.src.md
bin/local/generate-config-schema     # regenerate config.schema.json + the docs key reference from WorkbenchConfig
git/bin/local/generate-git-rules     # regenerate git.generated.md from lib/conventions.sh
otto-workbench changelog       # show recent changes from conventional commits
npm --prefix site run dev      # preview the docs site — localhost:3000/otto-workbench
npm --prefix site test         # run the site's remark plugin tests
npm --prefix site run build    # static-export the site to site/out (what CI's Site job runs)
```

Pre-push and CI run three gates independently — `bin/local/validate-all`, `bats tests/`,
and `pytest tests/`. Passing one is not passing the gate.

There is no virtualenv and no Python dependency manager here — `pyproject.toml` only sets
pytest's `testpaths`, and nothing is installed from it. Run `pytest` and `bats` as bare
commands off `PATH`, the way the Taskfile, CI, and the pre-push hook do. `uv run pytest`
and `.venv/bin/python -m pytest` reach nothing the bare command does not, and neither is
allow-listed, so they only add a permission prompt.

## Conventions

- **Single source of truth** — every piece of data or config has exactly one authoritative owner. Display logic reads from the owner; it does not duplicate or re-derive the data. Runtime choices (e.g. Docker runtime) are recorded in state files (`~/.local/state/workbench/`); checks should read state, not infer from binary presence. When defaults must appear in multiple formats (YAML + shell), add a cross-validation test. Registry `*.registry.yml` files own tool documentation (`tools[]`). Registry `*.env.yml` files own env var declarations (`env[]`, `auth`), colocated with the consumer code that reads them. Env vars set programmatically at runtime (e.g. DOCKER_HOST) are NOT declared in registries.
- Dynamic discovery over hardcoded config — glob patterns, not individual entries. Test: "does adding a new item require editing this file?" If yes, use a convention-based alternative.
- Adding a brew tool = add to Brewfile + registry.yml. No other config edits needed. Env vars go in a `.env.yml` next to the consumer, not in the brew registry.
- Adding a migration = create `<component>/migrations/YYYYMMDD-slug.sh` with a `migration_YYYYMMDD_slug()` function. No registry edits needed. Migrations must not source `lib/ui.sh` or assign `WORKBENCH_DIR` — both are provided by the migration framework (`lib/migrations.sh`).
- Generated files (`tools.generated*.md`, `git.generated.md`, `config.schema.json`) are never edited directly — edit the source and regenerate.
- Docs are composed, not spliced — a `docs/<name>.md` carrying a compose-docs banner is an artifact of `docs/<name>.src.md`. Prose goes in the `.src.md`; a generated section is an `<!-- include: bin/local/<generator> --emit <block> -->` directive, so a doc names the block it wants and no dispatch table sits between them.
- A `lib/` doc comment is published — the module's header block and the first paragraph of each function's comment are what `docs/libraries.md` prints. Keep the first paragraph to the contract, including what each argument does, and put implementation rationale below a blank comment line.
- Config files in `zsh/config.d/` use `# duplicate-check: <pattern>` headers to prevent overlapping concerns.
- **Idempotency is required** — all setup scripts, sync functions, and migrations must be safe to re-run. Guard installs with presence checks, use `install_symlink` (not raw `ln`), and ensure repeated execution produces the same result with no side effects.
- Migrations are state-tracked in `~/.local/state/workbench/migrations.applied` and auto-pruned when removed. A migration that drains a path under the config or state root must carry a `# adoption-sensitive: <reason>` header line — legacy-root adoption re-seeds such a path and would otherwise leave the work permanently undone (see `docs/execution-flow.md` § Migrations).
- A migration that edits files inside a repo must carry a `# project-scoped: <reason>` header line and take the repo path as its only argument — the framework then runs it once per registered repo and records a state line for each, so a repo that registers later still receives it. Never loop over the project registry inside a migration: that records one machine-wide line and skips every repo the machine learns about afterwards (enforced by `bin/local/validate-migrations`).
- **Portable stat calls** — `stat` format flags live in `lib/portable.sh`; call the portable stat helpers instead of invoking `stat -f`/`stat -c` directly (enforced by `bin/local/validate-stat-portability`).
- **Ignore entries stay true** — a path `.gitignore` covers must hold no tracked files. Git keeps tracking what it already tracks, so a rule added after the files were committed never applies to them and every later write into that directory is one `git add` from being committed (enforced by `bin/local/validate-tracked-ignored`). Untrack with `git rm --cached <path>`.
- **Live permission rules** — in `ai/claude/settings.json`, `Bash(cmd:*)` is literal prefix matching and `Bash(cmd *)` is a regex; a `*` written inside a `:*` prefix and a leading `~` are both matched literally, so such a rule silently never fires and the prompt it was meant to prevent keeps appearing (enforced by `bin/local/validate-permissions`).
- **Permission grants are tracked** — grants for this repo's own scripts live in the tracked `.claude/settings.json`, which every worktree inherits; `ai/claude/settings.json` is machine-wide and holds nothing repo-specific. A grant that ends up in the untracked `.claude/settings.local.json` means a rule that should have matched did not — move it, don't leave it. See `CONTRIBUTING.md` § Permission grants.
- **Documentation is part of the deliverable** — features, behavioral changes, and new tools must include doc updates before the PR is created. See `ai/guidelines/rules/general.md` (Comments & Documentation) for specifics.
- **PR descriptions use the repo template** — `.github/PULL_REQUEST_TEMPLATE.md` defines required sections (`## What`, `## Why`). Always structure PR bodies with these headers, whether creating via `task pr:create` or passing `--body` / `--body-file`. Never write freeform descriptions that omit the template sections.
- **Fix rules, not memories** — when a Claude behavior problem is identified in this repo, the fix is a rule change in `ai/guidelines/rules/` or `CLAUDE.md`, not a feedback memory. Rules in otto-workbench are the single source of truth for Claude behavior and apply globally via setup. Use memory only for things that genuinely don't belong in rules (user preferences, project context, references).
