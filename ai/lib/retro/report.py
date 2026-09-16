"""Rendering a retro scan's findings into the markdown report retro-scan prints.

Takes the scan's collected repos, per-rule match counts and cross-PR themes
and lays them out as headed sections — comments by repo, a rules-coverage
table, and repeated themes. Deciding which rule a comment is nearest to is
`retro.rules`'; this module only renders what was already decided.
"""

# doc-group: platform

from __future__ import annotations

from datetime import datetime

from retro.rules import TermWeights, best_passage, extract_keywords


# ── Constants ────────────────────────────────────────────────────────────────

COMMENT_BODY_MAX = 500

# How much of the matched passage the report quotes. A passage is a whole list
# item or table row, and the corpus runs to 1466 characters for the longest of
# them, which is a wall of text on a line a reader is scanning. The median is
# 153 and the 75th percentile 250, so this shows roughly seven passages in ten
# whole and keeps the rest to something a scan can read.
MATCH_SNIPPET_MAX = 200

DATE_FMT = "%Y-%m-%d"


# ── Report formatting ────────────────────────────────────────────────────────

def _format_comment_location(comment: dict) -> str:
    if not comment.get("path"):
        return ""
    loc = f" (inline: {comment['path']}"
    if comment.get("line"):
        loc += f":{comment['line']}"
    return loc + ")"


def _format_comment(comment: dict) -> list[str]:
    lines: list[str] = []
    location = _format_comment_location(comment)
    lines.append(f"- **Comment by @{comment['author']}**{location}")
    # Re-truncated defensively: every builder of this dict already caps `body`
    # at COMMENT_BODY_MAX, but this renderer has no way to enforce that on a
    # comment built elsewhere, so it does not trust the cap arrived intact.
    lines.append(f"  > {comment['body'][:COMMENT_BODY_MAX]}")
    if comment.get("direction"):
        lines.append(f"  - Direction: {comment['direction']}")
    nr = comment.get("nearest_rule")
    if nr and nr.get("match_snippet"):
        lines.append(f"  - Nearest rule: {nr['filename']} (\"{nr['match_snippet']}\")")
    elif nr:
        # No snippet means no passage of the rule cleared the shared-term floor
        # against this comment. The filename is all that is known, and quoting
        # an empty string beside it reads as a rule that states nothing.
        lines.append(f"  - Nearest rule: {nr['filename']}")
    else:
        lines.append("  - Nearest rule: (none)")
    lines.append("")
    return lines


def _format_pr(pr: dict) -> list[str]:
    if not pr.get("comments"):
        return []
    lines: list[str] = []
    lines.append(f"#### PR #{pr['number']}: {pr['title']} (merged {pr['merged_at']})")
    lines.append("")
    for comment in pr["comments"]:
        lines.extend(_format_comment(comment))
    return lines


def _format_theme(rule_file: str, examples: list[dict]) -> list[str]:
    lines = [f"### {rule_file} ({len(examples)} occurrences)", ""]
    for ex in examples[:5]:
        lines.append(f"- PR #{ex['pr']}: \"{ex['body'][:100]}\"")
    lines.append("")
    return lines


def format_report(scan_data: dict, version: str) -> str:
    """The scan's markdown report.

    `version` names the scanner build in the report's header comment; it comes
    from `_version` under `ai/bin/`, which this module — under
    `ai/lib/` — cannot import, so the caller resolves it and passes it in.
    """
    repos = scan_data.get("repos", [])
    rules_summary = scan_data.get("rules_summary", [])
    themes = scan_data.get("themes", {})

    total_prs = sum(len(r.get("prs", [])) for r in repos)
    all_comments = [
        c
        for r in repos for pr in r.get("prs", [])
        for c in pr.get("comments", [])
    ]
    total_comments = len(all_comments)

    direction_counts: dict[str, int] = {}
    for c in all_comments:
        d = c.get("direction", "")
        if d:
            direction_counts[d] = direction_counts.get(d, 0) + 1

    lines: list[str] = []
    lines.append("# Retro Scan Report")
    lines.append(f"<!-- generated: {datetime.now().strftime(DATE_FMT)} | scanner: {version} -->")
    lines.append(f"<!-- repos: {len(repos)} | prs: {total_prs} | comments: {total_comments} -->")
    dir_parts = [f"{k}: {v}" for k, v in sorted(direction_counts.items())]
    if dir_parts:
        lines.append(f"<!-- {' | '.join(dir_parts)} -->")
    lines.append("")

    if not repos or total_comments == 0:
        lines.append("No PR comments found in the scan window.")
        lines.append("")
        return "\n".join(lines)

    lines.append("## PR Comments by Repo")
    lines.append("")

    for repo in repos:
        repo_unmatched = repo.get("unmatched", 0)
        if repo_unmatched:
            lines.append(f"### {repo['github']} ({repo_unmatched} unmatched)")
        else:
            lines.append(f"### {repo['github']}")
        lines.append("")
        for pr in repo.get("prs", []):
            lines.extend(_format_pr(pr))

    lines.append("## Rules Coverage Summary")
    lines.append("")
    if rules_summary:
        lines.append("| Rule file | Matched comments |")
        lines.append("|-----------|-----------------|")
        for rs in rules_summary:
            lines.append(f"| {rs['filename']} | {rs['matched']} |")
        lines.append("")

    repeated = {k: v for k, v in themes.items() if len(v) >= 2}
    if repeated:
        lines.append("## Repeated Themes")
        lines.append("")
        for rule_file, examples in sorted(repeated.items()):
            lines.extend(_format_theme(rule_file, examples))

    return "\n".join(lines)


def format_matched_snippet(
    comment_body: str, rule: dict, weights: TermWeights,
) -> str:
    """The passage of `rule` that `comment_body` matched on, quotable.

    The snippet comes off the same per-passage scoring that chose the rule, so
    the report quotes the text the match was actually made on. A scan for the
    best `- ` bullet answered a different question and could not answer it at
    all for a rule file written in prose — `artifacts.md` and `self-review.md`
    state every rule they have without a single top-level bullet, and the old
    fallback showed the reader the filename it already had rather than any
    rule text.

    Empty when no passage of `rule` shares `MIN_SHARED_TERMS` with the
    comment. On the path both callers use that cannot happen — a rule reaches
    here only because `find_nearest_rule` scored it above the match floor, and
    a rule with no candidate passage scores zero — but the function is public
    and says what it does for a rule the scorer did not pick.

    Called by both retro-scan's GitHub-comment path and `retro.reviews`'
    local-finding path, so it is published rather than kept private to either.
    """
    match = best_passage(extract_keywords(comment_body), rule, weights)
    if match is None:
        return ""
    text = match.passage.text
    if len(text) <= MATCH_SNIPPET_MAX:
        return text
    return text[:MATCH_SNIPPET_MAX - 1] + "…"
