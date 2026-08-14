---
paths:
  - "ai/claude/bin/claude-review"
  - "ai/claude/bin/review-*"
  - "ai/claude/bin/ci-check"
  - "ai/claude/bin/pr"
  - "ai/lib/**"
  - "ai/claude/agents/reviewer.md"
---

# claude-review Development

When adding or modifying a review phase, verify these integration points:
- `review_common.py`: `SEVERITIES` list, `SeverityConfig` fields (`posting`, `body_group`, `section`, `aliases`), `severity_by_key()`
- `review-orchestrate`: `_FINDING_SECTIONS` (derived from registry), `renumber_section()`, `merge_reviews()`, `build_prompt()` template rendering
- `review-post`: `renumber_for_posting()`, `parse_findings()` parser, `classify_findings()` posting routing
- `agents/reviewer.md`: output format (Phase 10 markdown template), finding ID patterns (`[M1]`, `[S1]`, etc.)
- `lib/review-templates/`: section headers referenced in synthesis and group templates

## Re-review reconciliation

A re-review accounts for every prior finding in a `## Prior findings` ledger —
one line per prior finding, `- **[M1]** \`path\` — Fixed` or `— Still open`,
with the ID and path copied from the prior review.
`unaccounted_prior_findings()` matches a prior finding by an ID or path the
ledger names, or by the stable ID the new review's own finding lines hash to
(so a verbatim carry-forward matches with or without its `<!-- sid: -->`
marker); anything it cannot match is warned about. The ledger is unioned across
groups by `merge_reviews()`, copied through by the synthesis templates, and
stripped in `post_process_findings()` before renumbering — its IDs number the
prior review, not this one. Changing any one of `SECTION_PRIOR_FINDINGS`, the
merge, the synthesis templates, or `_build_prior_section()`'s instruction means
checking the other three.

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
| `pr comments --finish` | `review-threads --finish` | script (updates state directly) |
| `pr rebase` | `pr-rebase` | script (updates state directly) |
| `pr gc` | none (local via `review_gc`) | none |
| `pr fix` | `claude-review` (--fix), `ci-check` (--fix) | none |
| `pr status` | none (reads cached state) | none |

### Adding a new subcommand

1. Create the external script in `ai/claude/bin/`
2. Add argparse subparser in `pr`
3. Add `cmd_<name>` wrapper that delegates via `subprocess.run()`
4. If the subcommand has persistent state: add a `Domain` subclass to `pr_state.py`
   and a field for it on `PRState` — see State management below
5. Add `_render_<name>_section()` to `pr` for the `cmd_status` dashboard
6. Register in `ai/claude/registry.yml`
7. Add tests in `tests/`

### State management

- State file: `state.json` in the worktree's own git dir, under `workbench/` —
  resolve it with `workbench_paths.worktree_state_dir()`, never by hand
- Lib module: `ai/lib/pr_state.py`
- Each domain is a `pr_state.Domain` subclass (e.g., `CIDomain`, `RebaseSummary`)
  serialized via generic `serde.to_dict()`/`serde.from_dict()`
- **Adding a domain is one edit**: subclass `Domain` and add a field for it on
  `PRState`. The registry, the `apply` routing, and the `apply_state_update`
  domain name are all derived from that field's annotation
- Updated via `pr_state.apply(state, summary)` + `pr_state.save_state()`.
  `apply` routes by type to the field annotated with it
- A write replaces the stored domain. A domain that must accumulate across
  rounds overrides `merge_into` to fold the prior value in (see `FixSummary`)
- Scripts own their state updates — Python scripts import `pr_state` directly
- **Every reader goes through `pr_state.load_state()`** — never `json.load` on
  `state.json`. The dataclasses in `pr_state.py` are the schema; a raw-dict
  reader duplicates it and silently blanks when a field is renamed
- `load_state()` returns `None` for a missing file and an unreadable one alike,
  warning on the latter. Callers degrade; they do not need to tell them apart
- `pr_state.load_or_init()` provides DRY state loading across all scripts
- `pr_state.apply_state_update()` provides generic dict-based state updates
