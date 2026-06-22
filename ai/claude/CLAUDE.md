# Claude Code

## Git Commit & PR Rules

Never mention Claude Code, AI assistance, or co-authorship in commit messages, PR descriptions, or any git artifacts.

## Machine Profile

If `~/.claude/machine/machine.md` exists, read it at session start — it contains
hardware, OS, runtime versions, Docker setup, Git identity, and the project registry.
Use it to answer environment questions without re-discovering system state. Check
`<!-- last-updated -->`: if more than 7 days ago, mention it may be stale and suggest
running `/machine` to refresh. Auto-regenerates every 24h via Stop hook; run `/machine`
to force a refresh.

## Project Anatomy

If `.claude/anatomy.md` exists in the project, read it before exploring unfamiliar parts of the codebase. It contains a file index with descriptions and token estimates — use it to decide which files to open instead of browsing blindly. Regenerated automatically via Stop hook; run `/anatomy` to force a refresh.

If `.claude/context.md` exists in the project, read it alongside anatomy.md — it contains architecture narrative, service identity, and known constraints that anatomy.md does not capture. Check the `<!-- last-reviewed: -->` date at the top: if it is more than 30 days ago, note that context.md may be stale. When working on infrastructure tasks, explicitly state which service you are targeting and confirm its software identity against context.md before writing any tasks or config.

## Agent Protocols

When a situation matches an agent's domain, read the agent file and follow its
protocol before taking action. Agent files live at `~/.claude/agents/`.

| Situation | Agent file | Constraint |
|-----------|-----------|------------|
| Investigating a bug, test failure, or unexpected behavior | `debugger.md` | Diagnose before fixing |
| Production incident or outage triage | `incident.md` | Read-only investigation |
| Dependency upgrade or framework migration | `migrate.md` | Plan before changing |
| Code review (PR or diff) | `reviewer.md` | Review before approving |

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
| `pr review` | `claude-review` | script (updates state directly) |
| `pr comments` | `review-threads` | script (updates state directly) |
| `pr triage` | `review-thread-triage` | script (via `pr-state-update` CLI) |
| `pr rebase` | `pr-rebase` | script (updates state directly) |
| `pr repair` | `claude-review` (summary/rebuild) | `pr` wrapper |
| `pr post` | `claude-review` (post) | script (via `pr-state-update` CLI) |
| `pr gc` | `claude-review` (gc) | none |
| `pr fix` | `claude-review` (--fix) | none |
| `pr status` | none (reads cached state) | none |

### Adding a new subcommand

1. Create the external script in `ai/claude/bin/`
2. Add argparse subparser in `pr`
3. Add `cmd_<name>` wrapper that delegates via `subprocess.run()`
4. If the subcommand has persistent state: add a dataclass to `pr_state.py`
   with `_to_dict`/`_from_dict` serializers and an `update_<name>()` function
5. Add `_render_<name>_section()` to `pr` for the `cmd_status` dashboard
6. Register in `ai/claude/registry.yml`
7. Add tests in `tests/`

### State management

- State file: `<worktree>/ignore/pr/state.json`
- Lib module: `ai/claude/lib/pr_state.py`
- Each domain has a dataclass (e.g., `CISummary`, `RebaseSummary`) with
  `_to_dict`/`_from_dict` serializers
- Updated via `pr_state.update_<domain>(state, summary)` + `pr_state.save_state()`
- Scripts own their state updates — Python scripts import `pr_state` directly,
  bash scripts pipe JSON to `pr-state-update --domain <name> --repo-dir <path>`
- `pr_state.load_or_init()` provides DRY state loading across all scripts
- `pr_state.apply_state_update()` provides generic dict-based updates for the CLI helper

## Output

- Do not summarize changes at the end of a response — the diff speaks for itself
- When presenting options, keep descriptions to 1-2 sentences each
