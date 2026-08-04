# Review Sections Registry

Refactor `review-post` pipeline to use a config-driven section registry with auto-discovery, replacing per-section parameter threading.

## Problem

Adding a new review section (e.g., Static Analysis in PR #578) requires threading a new named parameter through 6+ function signatures across 3 files. The parameter is only extracted in one place and rendered in one place — everything else just passes it through.

## Solution

Two new abstractions in a new `review_sections.py` lib module:

1. **`SectionConfig`** — static, declarative definition of how a section is discovered and rendered
2. **`ReviewSections`** — runtime container carrying all extracted non-finding section content

A static `KNOWN_SECTIONS` list defines the registry. Auto-discovery handles unknown `## X` headers as passthrough sections with zero code changes.

## Data Model

### SectionConfig

Frozen dataclass defining a section's behavior:

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Unique identifier (e.g., `"summary"`) |
| `header` | `str` | Markdown `## Header` to match during extraction |
| `position` | `str` | `"before_findings"` or `"after_findings"` |
| `heading` | `str` | Heading to emit in rendered output (empty = none) |
| `strip_action` | `bool` | Strip verdict-style action prefix |
| `trailing_separator` | `bool` | Emit `---` after this section's group |
| `subsection_of` | `str` | Parent section key (groups rendering) |

### KNOWN_SECTIONS

Static registry list:

| Key | Header | Position | Heading | Notes |
|-----|--------|----------|---------|-------|
| `summary` | Summary | before_findings | `## Summary` | `trailing_separator=True` |
| `verdict` | Verdict | before_findings | `### Verdict` | `strip_action=True`, `subsection_of="summary"` |
| `static_analysis` | Static Analysis | after_findings | (none) | Passthrough |

### ReviewSections

Runtime container:

- `_entries: dict[str, str]` — key → extracted content
- `_configs: dict[str, SectionConfig]` — key → config (includes auto-discovered)

Class method `from_text(text: str) -> ReviewSections`:
1. Scan for all `## X` headers in the review markdown
2. Match against `KNOWN_SECTIONS` by header name (case-insensitive)
3. Skip severity headers (`Must fix`, `Should fix`, `Nit`, `Idioms` and their aliases)
4. Any unrecognized `## X` header gets a default `SectionConfig(position="after_findings", heading="", passthrough defaults)`
5. Extract content between matched header and next `## ` boundary

Instance methods:
- `get(key: str) -> str` — content by key, empty string if absent
- `before_findings() -> list[tuple[SectionConfig, str]]` — (config, content) pairs in declaration order, only those with content
- `after_findings() -> list[tuple[SectionConfig, str]]` — same for after-findings position

## Rendering

`format_body_text` signature changes from:

```python
def format_body_text(body_findings, has_inline, severity_filter,
                     summary="", verdict="", static_analysis="")
```

to:

```python
def format_body_text(body_findings, has_inline, severity_filter,
                     sections: ReviewSections | None = None)
```

Centralized rendering loop:

1. **Before-findings**: iterate `sections.before_findings()`. For each (config, content):
   - Emit `config.heading` if non-empty
   - Apply `strip_action` transform if flagged
   - Emit content
   - Track parent groups; emit `trailing_separator` after parent group completes
2. **Severity label line**: existing logic unchanged
3. **Finding body**: existing `by_sev` / `by_file` logic unchanged
4. **After-findings**: iterate `sections.after_findings()`. For each (config, content):
   - Emit blank line + content verbatim

Early-return condition: `not body_findings and not sections.after_findings()` replaces `not body_findings and not static_analysis`.

## Parameter Threading Elimination

Every function that currently threads `summary=""`, `verdict=""`, `static_analysis=""` changes to accept a single `sections: ReviewSections | None = None`:

| File | Functions affected |
|------|--------------------|
| `review-post` | `_run_post` (extracts via `ReviewSections.from_text`) |
| `review_format.py` | `format_body_text` |
| `review_posting.py` | `_post_and_track`, `_reclassify_and_retry`, `_post_as_comment`, `_format_comment_body` |

Where `verdict` is needed independently for tracking: `sections.get("verdict")`.

## Auto-Discovery

When `from_text` encounters a `## X` header that:
- Is NOT a severity section header (checked against `review_common.SEVERITIES` names + aliases)
- Is NOT in `KNOWN_SECTIONS`

It creates a default `SectionConfig`:
- `key`: slugified header (e.g., "Performance Notes" -> "performance_notes")
- `position`: "after_findings"
- `heading`: `## {original}` (re-emitted because `_extract_section` strips the header from content)
- All flags False

This means adding a new section to the review template requires zero code changes — it's auto-discovered and appended after findings.

## File Changes

| File | Change |
|------|--------|
| `ai/claude/lib/review_sections.py` | **New** — `SectionConfig`, `KNOWN_SECTIONS`, `ReviewSections` |
| `ai/claude/lib/review_format.py` | Replace section kwargs with `sections` param; loop-based rendering |
| `ai/claude/lib/review_posting.py` | Replace 3 kwargs with `sections` in 4 functions |
| `ai/claude/bin/review-post` | Replace `_extract_section` calls with `ReviewSections.from_text()`; pass single object |
| `tests/test_review_post.py` | Update existing tests; add `ReviewSections` unit + auto-discovery tests |

## Tests

### ReviewSections unit tests
- `from_text` extracts known sections correctly
- `from_text` auto-discovers unknown `## X` headers as passthrough
- `from_text` ignores severity headers
- `get()` returns empty string for absent sections
- `before_findings()` / `after_findings()` return configs in declaration order, only with content

### Rendering tests
- Existing `format_body_text` tests updated to use `sections=ReviewSections(...)`
- Static analysis appended after findings (existing test, updated interface)
- Static analysis with no findings (existing test, updated interface)
- Empty sections omitted (existing test, updated interface)

### Auto-discovery integration test
- Review file with a `## Performance Notes` section (not in `KNOWN_SECTIONS`)
- Verify it appears in the posted body after findings with zero code changes

### Dry-run integration test
- Existing `test_dry_run_includes_static_analysis` updated to use new interface
