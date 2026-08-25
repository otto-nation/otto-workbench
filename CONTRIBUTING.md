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
| [GNU parallel](https://www.gnu.org/software/parallel/) | `brew install parallel` |
| [pytest-xdist](https://pytest-xdist.readthedocs.io) | `pipx inject pytest pytest-xdist` |

The last two are what make the suites run in parallel. Neither is required — without
them `bin/local/run-tests` falls back to a serial run of the affected suite, which takes
several minutes rather than well under one.

## Running Tests

```bash
task test          # run both suites (bats, then pytest)
task test:pytest   # run only the pytest suite
task lint          # ShellCheck all shell scripts
```

Both go through `bin/local/run-tests`, which owns the parallelism settings for the whole
repo — the Taskfile, the pre-push hook, and CI all call it rather than spelling the flags
out themselves. It sizes the run from the cores the machine is *not* already using — the
core count less the one-minute load average, floored at 2 and capped at 12 — so a suite
started beside another one takes the share that one left rather than oversubscribing the
box. Set `TEST_JOBS` to take the sizing back, and `TEST_JOBS=1` to get the serial ordering
when bisecting a test that only fails under concurrency.

Sizing from load is what keeps concurrent whole-suite runs honest. Neither suite fails
gracefully when it cannot get scheduled: a subprocess that never runs surfaces as a
timeout, a SIGPIPE, or a git daemon that will not answer, and none of those name the
machine as the cause. When one does slip through, the shared runner in `tests/conftest.py`
raises `MachineContention` rather than a plain assertion and says so in the message — a
failure carrying that text is a machine to re-run on, not a defect to bisect.

Tests live in `tests/`. Each file targets a single library function or script behaviour. The shared helper `tests/test_helper.bash` provides `source_lib`, `make_ai_config`, `make_fake_binary`, and `make_git_remote`.

## Writing Tests

- Match the style of existing test files: `setup()` -> `source_lib` -> `@test` blocks.
- Use `run` + `$status` / `$output` for functions with side effects or exit codes.
- Call functions directly (without `run`) when asserting variable state.
- Use `TMPDIR="$(mktemp -d)"` in `setup()` and `rm -rf "$TMPDIR"` in `teardown()` for any filesystem work.
- Call `common_setup` first in `setup()`. A `setup_file()` that runs git needs its own call
  as well, because that hook runs before the first `setup()` does. It is what
  detaches a temp repo from the machine's own git config — `core.fsmonitor`, the global
  hooks path, everything in `~/.gitconfig` — so no test needs a guard of its own. A test
  that wants a global config with content in it re-exports `GIT_CONFIG_GLOBAL` afterwards,
  pointing at a file it wrote; `tests/test_helper_isolation.bats` pins both halves.
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
without it. `bin/local/validate-permissions` checks every settings file for
rules that can never match; `tests/claude_settings.bats` holds the project file
to directories the repo actually ships.

The same validator reports the drift itself. It reads the tracked project file
for the grants it already makes, then looks at the untracked ones — every
`settings.local.json` in the tree, plus the `.claude/` in the bare-repo
container above the worktrees, which no walk rooted in a worktree can reach.
Two classes fail, and a grant with no tracked home — a `WebFetch` domain, a
`/tmp` scratch script — is left alone by both.

| Class | Fix |
|---|---|
| A local `allow` a tracked `allow` already makes | `bin/local/validate-permissions --fix` deletes it |
| A local `allow` reaching an `ask`-gated command | a human deletes it, or decides the gate is wrong |

Both fail rather than warn, because a warning could not reach anyone here:
`validate-all` captures each validator's output and prints it only when that
validator exits non-zero, so on a green run the text is discarded before the
pre-push hook or CI renders it. Failing is what makes the finding visible, and
`--fix` is what keeps that defensible — the file regrows, so pruning it is a
command rather than an afternoon.

`--fix` prunes the first class only, and only from untracked files. Every entry
it removes is one a tracked rule already matches for every command the local
rule can match, so the set of commands the file permits is the same before and
after. An `ask` override is never pruned: deleting it restores a deliberate gate
on credential access, which is a person's call, not a script's.

### Reaching the container

At the container there is nothing to move a grant into: it holds no working
tree, so nothing there can be committed. That is also why grants land there in
the first place. Claude Code roots a project at the directory the session was
launched in, and in a bare-repo layout that is usually the container — so the
tracked `.claude/settings.json` a worktree carries never loads, every script in
a granted directory prompts, and each approved one-off is an exact full command
string that the next invocation with a different argument does not match.

`otto-workbench permissions mirror` closes that gap from the other side. It
copies the tracked file's `allow` and `ask` rules into a generated
`.claude/settings.json` at the container, so a session rooted there starts with
the grants the repo already reviewed. `otto-workbench ai sync` runs it, and it
is idempotent, so a stale mirror is one sync away from current.

Three things about that file are worth knowing before editing it:

- **It is generated.** A `_workbench` key records what the mirror wrote, so the
  next run replaces its own entries and leaves anything added by hand in place.
  Edit the tracked file and re-run; an edit to the mirror survives only until
  then.
- **Both buckets travel.** Copying `allow` without `ask` would deliver the grant
  on `bin/get-secret` and drop the prompt guarding it — a worse outcome than the
  prompts the mirror exists to remove.
- **One worktree speaks for the container.** A container holds many worktrees on
  many branches, any of them mid-edit; the one on the branch the shared
  repository's HEAD names is the reviewed copy, and it is the only one that
  writes. A container with no worktree of its own on that branch is reported
  and skipped.

Because those rules were reviewed in the tracked file they were copied from, the
mirror is not drift, and `validate-permissions` checks it for dead rules only. A
grant reaching the container anyway means the mirror is missing or stale — the
finding says to regenerate it.

So if a session launched from the container is prompting for this repo's own
scripts, run `otto-workbench permissions mirror` and start it again. There is
nothing to move by hand: once the mirror is there, a container-rooted session
and a worktree-rooted one start from the same grants.

Fewer sessions root there now. The `claude` shell wrapper in
[`zsh/config.d/tools/claude.zsh`](zsh/config.d/tools/claude.zsh) sends a launch
from the container into the worktree its default branch is checked out into, and
says so on the way. The mirror still matters — `command claude` bypasses the
wrapper, and a shell without the workbench's zsh config has no wrapper at all —
but the container is now where a session passes through rather than where it
settles.

### The rest of the machine

The validator only ever sees this repo, because the two things that run it are
this repo's: CI clones it alone, and its pre-push hook should not fail over a
stale grant in some other checkout. The drift is not this repo's, though —
`otto-workbench ai init` scaffolds a `.claude/` into every registered project
and the same accumulation happens in each one, unwatched.

`otto-workbench permissions sweep` walks the project registry instead, so the
unit is the machine rather than a repo. It asks the validator's two questions
of every untracked settings file it finds, plus a third the validator has no
use for.

| Class | Fix |
|---|---|
| A local `allow` reaching an `ask`-gated command | a human deletes it, or decides the gate is wrong |
| A local `allow` another rule already makes | `otto-workbench permissions sweep --prune` deletes it |
| A local `allow` naming a directory that is gone | a human deletes it |

Coverage is measured against two files there, not one. Per the paragraphs above
most registered repos have no tracked `settings.json` at all, so measuring only
against it would call every grant in them uncovered; the machine-wide
`~/.claude/settings.json` is the rule source that exists everywhere, and a grant
either file already makes is dead weight.

The third class needs neither file. A grant naming `/…/some-worktree/bin/thing`
when that directory is gone outlived its subject whatever else the machine
grants, and it is the class that explains why the counts only ever go up. It is
a heuristic — a rule can name a path for reasons this cannot see — so it is
reported and never pruned, and the report names the top of the missing subtree
rather than the file four levels down inside it.

The sweep exits 0 whether or not it found anything. It is a report, not a gate:
`otto-workbench maintenance` must not start failing the day some unrelated repo
accumulates its first covered grant. `--prune` is opt-in for the same reason the
validator's `--fix` is safe — the covered class is provably a no-op on effective
permissions — and it is the only class the sweep will touch inside a repo the
user did not ask it to visit.

The classification itself is `lib/permissions.py`, which the validator and the
sweep share. A second copy of the matcher is the thing that module exists to
prevent.

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
