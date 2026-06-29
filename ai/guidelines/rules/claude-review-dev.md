---
paths:
  - "ai/claude/bin/claude-review"
  - "ai/claude/bin/review-*"
  - "ai/claude/bin/ci-check"
  - "ai/claude/bin/pr"
  - "ai/claude/lib/**"
  - "ai/claude/agents/reviewer.md"
  - "lib/review-templates/**"
---

# claude-review Development

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
