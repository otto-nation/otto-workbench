# otto-workbench

Dotfiles and developer environment manager. Installs shell config, brew packages, git settings, Claude AI tooling, and editor config via a component framework.

## Stack

bash, zsh, python (the `ai/` subsystem — `ai/lib/`, `ai/bin/` and `ai/claude/bin/`), bats-core + pytest (tests), brew (packages), jq/yq (YAML/JSON), shellcheck (lint)

## Commands

```bash
bin/local/run-tests            # run both suites in parallel (what the Taskfile, pre-push, and CI call)
bin/local/run-tests --bats     # run only the bats suite
bin/local/run-tests --pytest   # run only the pytest suite
bats tests/<file>.bats         # run one bats suite
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

Pre-push and CI run three gates independently — `bin/local/validate-all`,
`bin/local/run-tests --bats`, and `bin/local/run-tests --pytest`. Passing one is not
passing the gate. The runner owns the parallelism for both suites, so a whole-suite run
goes through it rather than spelling `--jobs`/`-n` out again; a single file still goes
straight to `bats`/`pytest`.

There is no virtualenv and no Python dependency manager here — `pyproject.toml` only sets
pytest's `testpaths`, and nothing is installed from it. Run `pytest` and `bats` as bare
commands off `PATH`, the way the Taskfile, CI, and the pre-push hook do. `uv run pytest`
and `.venv/bin/python -m pytest` reach nothing the bare command does not, and neither is
allow-listed, so they only add a permission prompt.

`pytest-xdist` is the one Python package the suite wants but does not require:
`bin/local/run-tests` passes `-n` when it is importable and runs serially when it is not.
Install it into the same pipx venv as pytest — `pipx inject pytest pytest-xdist` — to get
the parallel run locally. CI installs it explicitly.

The runner sizes the run from the cores the machine is *not* already using: the core count
less the one-minute load average, floored at 2 and capped at 12. A second whole-suite run
therefore takes the share the first left rather than oversubscribing the box, which is
what neither suite survives — a test subprocess that cannot get scheduled surfaces as a
timeout, a SIGPIPE, or a git daemon that will not answer, in arbitrary tests that never
repeat. `TEST_JOBS` overrides the sizing outright, and `TEST_JOBS=1` restores the serial
ordering. When contention does get through, `tests/conftest.py` raises `MachineContention`
and names it in the message: re-run it, do not bisect it.

## Conventions

- **Single source of truth** — every piece of data or config has exactly one authoritative owner. Display logic reads from the owner; it does not duplicate or re-derive the data. Runtime choices (e.g. Docker runtime) are recorded in state files (`~/.local/state/workbench/`); checks should read state, not infer from binary presence. When defaults must appear in multiple formats (YAML + shell), add a cross-validation test. Registry `*.registry.yml` files own tool documentation (`tools[]`). Registry `*.env.yml` files own env var declarations (`env[]`, `auth`), colocated with the consumer code that reads them. Env vars set programmatically at runtime (e.g. DOCKER_HOST) are NOT declared in registries.
- Dynamic discovery over hardcoded config — glob patterns, not individual entries. Test: "does adding a new item require editing this file?" If yes, use a convention-based alternative.
- Adding a brew tool = add to Brewfile + registry.yml. No other config edits needed. Env vars go in a `.env.yml` next to the consumer, not in the brew registry.
- Adding a migration = create `<component>/migrations/YYYYMMDD-slug.sh` with a `migration_YYYYMMDD_slug()` function. No registry edits needed. Migrations must not source `lib/ui.sh` or assign `WORKBENCH_DIR` — both are provided by the migration framework (`lib/migrations.sh`).
- A migration returns `0` when it changed something and `MIGRATION_NOOP` when it found the target already in the shape it produces. Both are recorded and never retried; only a change is announced. Returning `0` for a no-op reports work that did not happen — which for a scoped migration is every sync, since each one visits the targets registered since the last (see `docs/execution-flow.md` § Migrations).
- A migration whose target does not exist *yet* returns `MIGRATION_DEFERRED` instead — nothing is recorded, nothing is printed, and the next sync asks again once the file has been written. `MIGRATION_NOOP` there retires the migration against a file it never saw, which is how the issue-tracker lift missed a `config.yml` a session created half an hour after it ran. `bin/local/validate-migrations` rejects a bare `return 0` under an absent-path guard and accepts `MIGRATION_DEFERRED` only from one, so the status nothing records cannot be reached from a condition that never resolves. A migration that *removes* something stays `MIGRATION_NOOP`: a file arriving later is a fresh install whose contents the operator chose.
- Generated files (`tools.generated*.md`, `git.generated.md`, `config.schema.json`) are never edited directly — edit the source and regenerate.
- Docs are composed, not spliced — a `docs/<name>.md` carrying a compose-docs banner is an artifact of `docs/<name>.src.md`. Prose goes in the `.src.md`; a generated section is an `<!-- include: bin/local/<generator> --emit <block> -->` directive, so a doc names the block it wants and no dispatch table sits between them.
- A `lib/` doc comment is published — the module's header block and the first paragraph of each function's comment are what `docs/libraries.md` prints. Keep the first paragraph to the contract, including what each argument does, and put implementation rationale below a blank comment line.
- **A lifted helper's return type is new** — moving a helper onto a `lib/` module's published surface republishes everything about it, its return type included, so `ai/guidelines/rules/general.md` § Types Over Tuples applies from that moment. The pre-existing-tuple exemption there covers a tuple left where it is, not one a refactor renames, relocates, or promotes: `_section_bounds` returning `(start, end)` inside `review_findings` was grandfathered, and `section_span` returning the same pair from `review_document` was a new tuple and took a frozen dataclass. Give it the type in the tranche that lifts it — every caller the lift exists to attract is one the tuple would teach to unpack by position. This one is prose, not a validator: `ai/lib/` carries some forty grandfathered tuple returns, so a check would ship as an allowlist rather than a rule.
- Config files in `zsh/config.d/` use `# duplicate-check: <pattern>` headers to prevent overlapping concerns.
- **Idempotency is required** — all setup scripts, sync functions, and migrations must be safe to re-run. Guard installs with presence checks, use `install_symlink` (not raw `ln`), and ensure repeated execution produces the same result with no side effects.
- Migrations are state-tracked in `~/.local/state/workbench/migrations.applied` and auto-pruned when removed. A migration that drains a path under the config or state root must carry a `# adoption-sensitive: <reason>` header line — legacy-root adoption re-seeds such a path and would otherwise leave the work permanently undone (see `docs/execution-flow.md` § Migrations).
- A migration that edits files inside a repo declares the scope it is done at, and takes the path the framework hands it as its only argument. `# checkout-scoped: <reason>` runs it once per registered work tree and records a state line per work tree — right for anything under a checkout's own `.claude/`. `# repo-scoped: <reason>` runs it once per repo, passing one of that repo's registered work trees and recording the state line against the shared git dir they all have in common — right for anything the checkouts share, such as a file at a bare-repo container, which would otherwise cost a visit and a state line per worktree for one deletion. A file may carry one marker, not both. Never loop over the project registry inside a migration: that records one machine-wide line and skips every repo the machine learns about afterwards (enforced by `bin/local/validate-migrations`).
- **One module object per script** — `tests/conftest.py` owns module execution: `load_script(name, path)` for an extensionless script under `bin/`, `bin/local/`, `ai/bin/` or `ai/claude/bin/`, `exec_fresh(name, path)` when the test's subject is what the body does while it runs, and `_load_lib(name)` for `lib/<name>.py`. A test that executes a script itself builds a second module object for it, and `mock.patch("<name>.f")` then rewrites the copy `sys.modules` holds while the call under test reads the other's globals — a silent no-op mock that surfaces only when xdist puts both files in one worker (enforced by `bin/local/validate-script-loading`).
- **Portable stat calls** — `stat` format flags live in `lib/portable.sh`; call the portable stat helpers instead of invoking `stat -f`/`stat -c` directly (enforced by `bin/local/validate-stat-portability`).
- **Ignore entries stay true** — a path `.gitignore` covers must hold no tracked files. Git keeps tracking what it already tracks, so a rule added after the files were committed never applies to them and every later write into that directory is one `git add` from being committed (enforced by `bin/local/validate-tracked-ignored`). Untrack with `git rm --cached <path>`.
- **Live permission rules** — in `ai/claude/settings.json`, `Bash(cmd:*)` is literal prefix matching and `Bash(cmd *)` is a regex; a `*` written inside a `:*` prefix and a leading `~` are both matched literally, so such a rule silently never fires and the prompt it was meant to prevent keeps appearing (enforced by `bin/local/validate-permissions`).
- **Committed allow buckets stay in codepoint order** — two paths put `allow` in that order and neither touches `deny` or `ask`: `ai/claude/steps.sh` merges the registry grants in with `jq`'s `unique`, which sorts, and Claude Code writes the bucket back sorted when a session grants a permission. A tracked settings file committed in any other order therefore comes back modified with no rule added and none removed, and that phantom diff belongs to no branch — it surfaces in every worktree at once and reads as uncommitted work in each. Add a rule in sorted position rather than at the end; `bin/local/validate-permissions --fix` sorts the bucket, and the check covers tracked files only — the container mirror is generated with the managed rules deliberately in front (enforced by `bin/local/validate-permissions`).
- **Permission grants are tracked** — grants for this repo's own scripts live in the tracked `.claude/settings.json`, which every worktree inherits; `ai/claude/settings.json` is machine-wide and holds nothing repo-specific. A grant that ends up in the untracked `.claude/settings.local.json` means a rule that should have matched did not — move it, don't leave it. `bin/local/validate-permissions` fails on a local grant the tracked file already makes (including in the bare-repo container above the worktrees) — run it with `--fix` to prune those — and on a local `allow` that re-grants an `ask`-gated script, which only a human deletes. The container's own `.claude/settings.json` is generated — `otto-workbench permissions mirror` copies the tracked file's rules there so a session launched from the container loads them at all, and `ai sync` runs it; edit the tracked file and re-sync rather than the mirror. See `CONTRIBUTING.md` § Permission grants.
- **Documentation is part of the deliverable** — features, behavioral changes, and new tools must include doc updates before the PR is created. See `ai/guidelines/rules/general.md` (Comments & Documentation) for specifics.
- **PR descriptions use the repo template** — `.github/PULL_REQUEST_TEMPLATE.md` defines required sections (`## What`, `## Why`). Always structure PR bodies with these headers, whether creating via `task pr:create` or passing `--body` / `--body-file`. Never write freeform descriptions that omit the template sections.
- **Fix rules, not memories** — when a Claude behavior problem is identified in this repo, the fix is a rule change in `ai/guidelines/rules/` or `CLAUDE.md`, not a feedback memory. Rules in otto-workbench are the single source of truth for Claude behavior and apply globally via setup. Use memory only for things that genuinely don't belong in rules (user preferences, project context, references).
- **`ai/skills/` holds skills and nothing else** — the tree is harness-neutral and installs into both `~/.claude/skills/` and `~/.agents/skills/` from `ai/skills/steps.sh`. Every consumer enumerates it with a `*/` glob, so any subdirectory added here is read as a skill: a `migrations/` directory would be installed into both harnesses as one. Migrations for this subsystem live under the harness whose path they drain.
- **`ai/guidelines/rules/` holds rules and nothing else** — it is the first of the three layers `resolve_rules` merges, each enumerated with a `*.md` glob: the repo's defaults here, the `workbench.md` that `workbench-rules sync` generates under the state root, and the operator's overrides under the config root — last, so a file they wrote by hand beats both and a `.disabled` sentinel there suppresses either. Every harness installs that one merged set and reads no other harness's output — `step_claude_rules` symlinks it into `~/.claude/rules/`, `step_pi_guidelines` concatenates the always-on ones into `~/.pi/agent/AGENTS.md` — so neither depends on the other having run, and a machine with only one of them installed still gets its rules. A note or a template dropped here is installed into both harnesses as a rule. Which harnesses a rule reaches is decided by its own frontmatter — see `rules-authoring.md` § Which harnesses a rule reaches.
