# Contributing

## Setup

```bash
git clone https://github.com/otto-nation/otto-workbench ~/otto-workbench
cd ~/otto-workbench
task dev:setup
```

`task dev:setup` activates the git hooks in `git/hooks/`, which run ShellCheck, validate registries, and regenerate tool context before every push.

## Dev Dependencies

Required to run the full test and lint suite:

| Tool | Install |
|------|---------|
| [bats-core](https://bats-core.readthedocs.io) | `brew install bats-core` |
| [ShellCheck](https://www.shellcheck.net) | `brew install shellcheck` |

## Running Tests

```bash
task test   # run full bats suite
task lint   # ShellCheck all shell scripts
```

Tests live in `tests/`. Each file targets a single library function or script behaviour. The shared helper `tests/test_helper.bash` provides `source_lib`, `make_ai_config`, `make_fake_binary`, and `make_git_remote`.

## Writing Tests

- Match the style of existing test files: `setup()` -> `source_lib` -> `@test` blocks.
- Use `run` + `$status` / `$output` for functions with side effects or exit codes.
- Call functions directly (without `run`) when asserting variable state.
- Use `TMPDIR="$(mktemp -d)"` in `setup()` and `rm -rf "$TMPDIR"` in `teardown()` for any filesystem work.
- Name tests in plain English describing the expected behaviour, e.g. `"omits a chunk that would exceed the budget"`.

When adding a new library function, add a corresponding test file `tests/<function_name>.bats`.

## Documenting Scripts

Every script and function should have a header comment that explains:
- What the script does (one line)
- Usage / arguments
- Any environment variables it reads
- Non-obvious side effects

Functions follow the pattern:
```bash
# function_name ARG — one-line description of what it does.
# Additional detail if the behaviour is non-obvious.
function_name() { ... }
```

## Adding a Component

See the [Component Framework](docs/components.md) reference for the full Tier 1/Tier 2 contract, required files, examples, and the `sync_<name>()` contract.

## Adding a Tool to the Registry

See [Registries](docs/registries.md#adding-an-entry) for the full schema, validation modes, and step-by-step instructions.

## Permission grants

Claude Code reads permission rules from three files, and which one a grant belongs
in is decided by how far it should reach.

| File | Reach | Holds |
|---|---|---|
| `.claude/settings.json` | this repo, every worktree | grants for the scripts this repo ships |
| `ai/claude/settings.json` | every repo on the machine, via `~/.claude/settings.json` | shell builtins, filesystem ops, and registry-derived tool grants |
| `.claude/settings.local.json` | one checkout, untracked | nothing worth keeping |

The tracked project file grants three directories — `bin/`, `git/bin/`, and
`ai/claude/bin/`. That is arbitrary execution of anything the repo ships in a bin
directory, and it is deliberate: it is the trust level a checkout of this repo
already implies, and a directory wildcard means adding a script needs no
allowlist edit. It is not the machine template's to grant, because a rule there
would also cover a repo cloned five minutes ago.

The grant stops at this repo on purpose. `otto-workbench ai init` scaffolds a
`.claude/` into other projects but deliberately writes no `settings.json` there:
a project that follows the same `bin/local/` convention prompts on every call
until someone decides, for that repo, that running what it ships unattended is
acceptable. Scaffolding the grant would put the decision back where this section
just took it from — a rule applied to repos nobody has looked at.

Two scripts are carved back out with an `ask` rule, which outranks `allow`:
`bin/get-secret` reads values out of AWS Secrets Manager and `bin/gcloud-reauth`
rewrites GCP application-default credentials. Neither should run without a human
seeing the call, and `ask` restores that prompt without taking the script away —
which is what a `deny` rule would do. Add to the list when a new script in a
granted directory reaches credentials.

Rules match the command as written, so a script must be invoked by its
repo-root-relative path — `bin/local/validate-all`, never `./bin/local/…` and
never an absolute path. `claude-bash-guard` blocks both of the other forms and
names the one that matches.

A grant that lands in `.claude/settings.local.json` is a bug report: some rule
that should have matched did not. Move it into one of the tracked files rather
than leaving it there, where nothing reviews it and the next worktree starts
without it. `bin/local/validate-permissions` checks every tracked settings file
for rules that can never match; `tests/claude_settings.bats` holds the project
file to directories the repo actually ships.

## Environment Variables

| Variable | Where set | Effect |
|----------|-----------|--------|
| `SYMLINK_MODE=no-prompt` | `bin/otto-workbench sync` | Skips the interactive overwrite prompt in `install_symlink` — real files at the target path are warned about and skipped instead of prompting |
| `NO_COLOR` | shell environment | Disables all ANSI color output from `lib/ui.sh` helpers (follows [no-color.org](https://no-color.org)) |
| `WORKBENCH_DIR` | auto-derived or caller | Override the repo root; set by `install.sh` and auto-derived from `lib/constants.sh` otherwise |

## Versioning & breaking changes

Two packages release independently via release-please: `otto-workbench` (repo root)
and `otto-ai-tools` (`ai/claude`).

### What is public

| Package | Public surface |
|---|---|
| `otto-workbench` | Command names installed to `~/.local/bin`, config keys and enum values in `config.schema.json`, component names in `install.components` |
| `otto-ai-tools` | Command names from `ai/claude/registry.yml`, shipped agent and skill names, top-level `settings.json` keys |

`bin/local/generate-public-surface` renders this into `public-surface.json` and
`ai/claude/public-surface.json`. Both are generated — never edit them by hand.
`bin/local/validate-public-surface` (run by `bin/local/validate-all`) fails when
either is stale.

Repo-internal scripts (`bin/local/*`, anything in a registry with
`meta.scope: workbench`) and third-party tools installed via brew are not public.

On-disk state and config layout is breaking **unless the change ships a migration**.
The migration framework is this repo's compatibility mechanism for on-disk change.

### Declaring a breaking change

Put the footer in the **commit body**:

```
BREAKING CHANGE: `pr review --post` renamed to `--publish`
```

`BREAKING-CHANGE:` (hyphenated) means exactly the same thing — Conventional Commits
v1.0.0 lists it as a synonym, release-please honours it, and so does the gate.

A `!` in the header (`feat!: …`) is encouraged for readability but never enough on
its own. This repo squash-merges with `COMMIT_OR_PR_TITLE`, so on a multi-commit PR
the PR title replaces your subject and a header-only marker is silently lost. Commit
bodies are always concatenated into the squashed message, so the footer survives.
`bin/local/check-surface-compat` fails a `!` header with no matching footer.

If an entry disappears but the change genuinely is not breaking — an internal tool
that was wrongly marked public, or a rename that ships a back-compat alias — declare
that instead, one footer per removed entry:

```
Not-Breaking: command:old-name — renamed, old name still symlinked
```

The reason is required — a `Not-Breaking:` footer with nothing after the separator
declares nothing, because the reason reaching git history is the whole point of a
footer over a checked-in allowlist. An en dash or a plain `-`/`--` separates the
entry from the reason just as well as the em dash above; a colon does not. The
entry is everything up to the *first* separator, so it may contain spaces and the
reason may contain further dashes.

The separator is required too. A footer carrying no separator at all — the whole
line read as one entry, or as one reason — is not a declaration the gate can act
on, so it is skipped in silence and the removal it meant to cover still fails the
push. If the gate reports an entry you thought you had declared, check the
separator before anything else.

### The gate

`bin/local/check-surface-compat` diffs the snapshots against the ones committed at
the merge base with `origin/main`, and fails an undeclared removal. It reads the
head side twice — your working tree *and* `HEAD` — and treats an entry as removed
when either read has lost it. Both halves are load-bearing: the working-tree read
is what lets `task commit` consult the gate before a commit exists, and the `HEAD`
read is what `git push` actually publishes, so restoring a snapshot in the tree
without committing it (a stash, an uncommitted `git revert --no-commit`) cannot
turn pre-push green. Deleting a snapshot outright counts as removing every entry
it held. The gate runs in pre-push and in the `Surface Compatibility` CI job; run
it by hand with `bin/local/check-surface-compat [--base REF]`.

Exit 0 and 1 are its verdict — anything else (2 for a usage error or a non-blob
snapshot at the merge base, 5 for a jq failure, 128 for a git one) means the check
never completed rather than that it passed. Both callers treat every non-zero
status as a hard fail.

### `task commit` catches it early

`task --global commit` runs the same gate before asking the AI to write the
message — this is the call the working-tree read exists for, since at that point
the commit that would carry the footer does not exist yet. If it finds an
undeclared removal, the AI is told which entries disappeared and is instructed to
add either a `BREAKING CHANGE:` footer or a `Not-Breaking:` footer per entry,
depending on whether the removal is actually breaking — so you see the correction
while writing the commit, not as a pre-push rejection afterward.

The gate is advisory here: it never blocks the commit, and the hard check still
happens at push time. Four ways the hint does not appear, none of which stop you
committing:

- **No gate binary**, or one that is not executable — silent, since a repo that
  sources this library without shipping the gate is not misconfigured.
- **The gate ran but could not finish** — no `origin/main` to diff against, not a
  git repo, a malformed snapshot at the merge base. `task commit` prints a
  one-line note to stderr and writes the message without the hint.
- **The snapshot is stale.** Nothing regenerates it for you: run
  `bin/local/generate-public-surface` before `task commit` if you just removed
  something. Until you do, the working-tree snapshot still lists the old entry, so
  the gate sees no removal and has nothing to report — the hint appears on the
  *next* commit, once the snapshot is caught up. `validate-public-surface` (part
  of pre-push) is what catches the staleness itself.
- **The removal is already declared** — a footer covering every removed entry is
  the passing case, and the gate stays quiet.

### Commits that touch both packages

A single commit touching both packages majors **both**: release-please has no
per-package footer syntax. Split the commit if you only mean to break one.

## Code Conventions

- Quote all variables: `"$VAR"` not `$VAR`.
- Use `[[` instead of `[` for conditionals.
- Use `set -e` or explicit error checks in scripts.
- No magic values — use named variables or constants from [`lib/constants.sh`](lib/constants.sh).
- Guard clauses and early returns over nested `if` blocks.
- Colors: `RED`=error `YELLOW`=warn `GREEN`=success `BLUE`=info `CYAN`=section label `DIM`=metadata.
