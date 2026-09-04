---
paths:
  - "ai/bin/claude-review"
  - "ai/bin/review-*"
  - "ai/bin/ci-check"
  - "ai/bin/pr"
  - "ai/lib/**"
  - "ai/claude/agents/reviewer.md"
---

# claude-review Development

When adding or modifying a review phase, verify these integration points:
- `review/types.py`: `SEVERITIES` list, `SeverityConfig` fields (`posting`, `body_group`, `section`, `aliases`), `severity_by_key()`
- `review/merge.py`: iteration over `SEVERITIES` in `_Merge` and `merge_reviews()` — merging the group reviews into one document. Renumbering reads and rewrites every severity section together, so a new severity has to be in `SEVERITIES` for a reference to it to survive the merge
- `review/reconcile.py`: reconciliation against the prior review — `reconcile()`, `passed_over()`, `record_prior_findings()`. The ledger's other half is `review/reply_threads.py`: what the author did with the thread each posted finding opened (`fetch_reply_threads()`, `ReplyState`)
- `review/registry.py`: a builder in its phase table keyed by the new `Phase` — the template and the output path come off the phase spec, so the builder supplies neither
- `review/grammar.py`: the finding-line grammar (`FINDING_ID_RE`, `finding_location()`, `parse_finding_line()`) and the identity two findings are compared on (`FindingIdentity`, which owns both the dedup key and the stable ID). A pass that needs to read a finding line adds its selection pattern here rather than compiling one of its own
- `review/document.py`: `ReviewDocument.findings`, the one reading every consumer of a review's findings goes through
- `review/spans.py`: where a finding *stops* — `ends_finding_body()`, `finding_spans()`, `drop_findings()`, `cut_spans()`. A pass that walks a review a finding at a time asks these rather than recognising the next head itself, and keeps only its own selection pattern (which findings it wants) over `FindingSpan.line`. Six passes once measured a body for themselves and cut the same review four different ways, one of them deleting the resolved finding below a dropped one
- `review-post`: `renumber_for_posting()`, `classify_findings()` posting routing. `posted_id` is positional — reassigned on every round from the diff order — so nothing that has to survive to the next round may key on it. What survives is `FindingIdentity.stable_id`, which `format_inline_comment` writes into the posted comment as a `<!-- sid: -->` marker and `_match_thread_to_finding` reads back out. A pass comparing the words of a posted comment strips it first, the way `review_dedup` does; the fresh finding it is scored against carries none
- `agents/reviewer.md`: output format (Phase 10 markdown template), finding ID patterns (`[M1]`, `[S1]`, etc.)
- `lib/review-templates/`: section headers referenced in synthesis and group templates

## Re-review reconciliation

A re-review accounts for every prior finding in a `## Prior findings` ledger —
one line per prior finding, `- **[M1]** \`path\` — Fixed`, `— Still open`, or
`— Declined`, with the ID and path copied from the prior review. Those three
verdicts are `PriorDisposition`, and `review.prompt_prior._build_prior_section()`'s
instruction interpolates the enum's values, so the words asked for and the
words parsed cannot drift apart. Where the verdict sits in the line is held together by a
test instead: `TestLedgerInstructionParses` reads every example the instruction
shows back through `review.grammar.parse_ledger_line`, because an example the parser rejects
is invisible until a whole re-review's bookkeeping is lost. A verdict parses
when it comes first and ends the line or breaks with one of
`DISPOSITION_TAIL_PUNCTUATION`; a comma is deliberately not on that list, so
"Fixed, but only on the happy path" reaches no verdict rather than its
optimistic half.
The ledger is unioned across groups by `merge_reviews()` — where the strongest
verdict wins, in `PriorDisposition.precedence` order (`Declined` beats
`Still open` beats `Fixed`) — copied through by the synthesis templates, and
stripped in `post_process_findings()` before renumbering, since its IDs number
the prior review, not this one. Changing any one of `SECTION_PRIOR_FINDINGS`,
`PriorDisposition`, the merge, the synthesis templates, or
`_build_prior_section()`'s instruction means checking the others.

`review.reconcile.reconcile()` gives every prior finding a disposition, and
`record_prior_findings()` runs it from `_post_process_review()` — before the
strip, which is the last moment the review still says what it made of them.
Sources, in the order they are asked: a ledger entry matching the prior
finding's `FindingRef` (ID plus path); the stable ID the new review's own
finding lines hash to, so a verbatim carry-forward counts with or without its
`<!-- sid: -->` marker; then the tree, which settles a finding whose file is
gone, or whose quoted code was in that file at the prior review's `head_sha`
and is not in it now. `DispositionSource` records which of those answered, so
an inference is never read back as something the review stated, and the tree is
asked last because it cannot produce `Declined`. Every source reads a location
through `review.grammar.FindingIdentity`, which asks `finding_location()` first
so a finding citing a bare `` `path` `` with no `:<line>` still yields a path
and a stable ID — without one it can be neither carried forward nor checked
against the tree. That one type answers both the dedup key and the stable ID,
so two findings cannot be duplicates for one pass and distinct for the other.

What none of them settles is undecided, and `UndecidedReason` says which kind:
an unreadable ledger verdict and a location nothing could parse are defects
here, `NOT_CHECKABLE` is a check there was nothing to run, and only
`NOT_MENTIONED` is the review passing a finding by. `_report()` prints them
grouped in that order rather than as one list — run together they all read as
the last one — and the whole reconciliation, settled and not, is written to
`prior-findings.json`.

Because the ledger is stripped, `Declined` also has to survive on the finding
line itself: a declined finding is carried forward annotated
`*(declined — reason)*`, which `match_decline()` parses into `Finding.declined`
— anchored to the head of the finding body or the end of the line, so a
finding whose prose quotes the annotation is not silently adjudicated.
That flag is what keeps the finding out of `run_fix_pass`'s work set: a
declined finding is never rendered onto the tracking file and never costs the
turns it would have bought. The fix template treats a `ceiling:` or
`ceiling-permanent:` marked tradeoff as grounds for the agent's own `declined`
box rather than a defect to fix.

Posting reads the same two marks and answers them differently, because they are
not the same case. A ticked box is work that is done, so `review-post` drops the
finding before it classifies anything — `ReviewDocument.open_findings` is the
reading it takes, and a review whose findings are all ticked posts nothing at
all. A decline is a judgement rather than work, so it is stated: it is skipped
by `classify_findings()` whatever the diff says about its line, and rendered by
`format_body_text()` under its own heading in the review body, apart from the
findings the review is asking for. The ordering is what makes either reachable —
posting normally precedes any fix pass — so both marks arrive only on a review
posted after one, which is the run these two answers exist for.

`*(skipped — reason)*` is the second annotation vocabulary, written by the fix
pass rather than by a review, and `is_skipped()` is its single owner. It is
what `review.fix._apply_outcomes` writes back for a `needs a person` outcome,
the way it writes `*(declined — reason)*` for a declined one and ticks the box
for a fix. A skip still belongs to the work set — the next `--fix` retries it —
but a line already carrying either annotation is left exactly as it is: that
verdict was reached before this pass ran, and a second annotation on one line
leaves the document saying two things about one finding.

The pass itself is `fix_engine`'s: `ReviewFixAdapter` supplies the open
findings, the path-scoped commit and the re-render, and the batching, the
agent, the retry and the landing are the engine's. The agent answers on a
generated tracking file rather than on `review.md`, so what the review document
says is what the pass decided rather than what an agent happened to edit.

## Debugging claude-review

Review artifacts live in `~/.local/state/workbench/reviews/{repo}-{pr_or_branch}/`:

| File | Survives success | Purpose |
|------|-----------------|---------|
| `review.md` | yes | Final review output |
| `meta.json` | yes | PR metadata sidecar |
| `session.jsonl` | yes | Agent cost/usage/errors |
| `prompt-stats.json` | yes | Prompt composition diagnostics |
| `prior-findings.json` | yes | What became of each prior finding, and what settled it |
| `prompt-*.md` | no (kept on failure) | Full prompts sent to agents |
| `pipeline.json` | no | Resume state for multi-phase |

The review's trail is not in this directory. Every script appends to one root —
`~/.local/state/workbench/trail/YYYY-MM.jsonl`. Read it with
`otto-log query --pr <n>`.

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

The `pr` script (`ai/bin/pr`) is a thin dispatch layer. Each subcommand
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
| `pr comments --finish` | `review-threads --finish` | script (updates state directly) |
| `pr rebase` | `pr-rebase` | script (updates state directly) |
| `pr gc` | none (local via `review_gc`) | none |
| `pr fix` | `claude-review` (--fix), `ci-check` (--fix) | none |
| `pr status` | none (reads cached state) | none |

### Adding a new subcommand

1. Create the external script in `ai/bin/`
2. Add argparse subparser in `pr`
3. Add `cmd_<name>` wrapper that delegates via `subprocess.run()`
4. If the subcommand has persistent state: add a `Domain` subclass to `pr/domains.py`
   and a field for it on `PRState` — see State management below
5. Add `_render_<name>_section()` to `pr` for the `cmd_status` dashboard
6. Register in `ai/claude/registry.yml`
7. Add tests in `tests/`

### State management

- State file: `<state_dir()>/pr/<repo-key>-<branch-slug>/state.json` — keyed on
  the run's target, not on the checkout it was invoked from. Resolve it once via
  `pr_context.resolve()` and read `ctx.target_dir`; never rebuild the path
- Lib modules stack in one direction: `ai/lib/pr/fix.py` holds the fix vocabulary
  (`FixOutcome`, `ItemOutcome`, `FixRecord`, `CommitStatus`) and imports none of the
  others; `ai/lib/pr/domains.py` holds the domains and imports it;
  `ai/lib/pr/comments_fix.py` holds the comment domain and imports
  the domains; `ai/lib/pr/state.py` holds the envelope over them, the registry and
  the state file I/O, and imports both — never the other way round
- Each domain is a `pr_domains.Domain` subclass (e.g., `CIDomain`, `RebaseSummary`)
  serialized via generic `serde.to_dict()`/`serde.from_dict()`
- **Adding a domain is two lines in two files**: subclass `Domain` in `pr_domains`
  and add a field for it on `PRState`. The registry, the `apply` routing, and the
  `apply_state_update` domain name are all derived from that field's annotation
- Updated via `pr_state.apply(state, summary)` + `pr_state.save_state()`.
  `apply` routes by type to the field annotated with it
- A write replaces the stored domain, except for the `fix` record every domain
  carries — `Domain.merge_into` folds that one in for everybody. A domain with
  more to accumulate overrides `merge_into` and chains through `super()` (see
  `FixSummary` in `pr/comments_fix.py`)
- What a fix pass did about one item is a `pr.fix.ItemOutcome` on that record, not
  a new field on the domain. A fact about the item as the domain fetched it — a
  reviewer login, a job name — is not an outcome; keep it on the domain keyed by
  outcome id (see `FixSummary.reviewers`) rather than widening `ItemOutcome` with
  a field the other passes leave empty
- Scripts own their state updates — Python scripts import `pr_state` directly
- **Every reader goes through `pr_state.load_state()`** — never `json.load` on
  `state.json`. The dataclasses in `pr/domains.py` are the schema; a raw-dict
  reader duplicates it and silently blanks when a field is renamed
- `load_state()` returns `None` for a missing file and an unreadable one alike,
  warning on the latter. Callers degrade; they do not need to tell them apart
- `pr_state.load_or_init()` provides DRY state loading across all scripts
- `pr_state.apply_state_update()` provides generic dict-based state updates
