"""Rendering a retro scan's findings into the markdown report retro-scan prints.

Takes the scan's collected repos, per-rule match counts and cross-PR themes
and lays them out as headed sections — comments by repo, a rules-coverage
table, and repeated themes. Deciding which rule a comment is nearest to is
`retro_rules`'; this module only renders what was already decided.
"""

# doc-group: platform

from __future__ import annotations

from datetime import datetime

from retro.rules import extract_keywords


# ── Constants ────────────────────────────────────────────────────────────────

COMMENT_BODY_MAX = 500

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
    if nr:
        lines.append(f"  - Nearest rule: {nr['filename']} (\"{nr.get('match_snippet', '')}\")")
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


def best_matching_bullet(comment_body: str, rule: dict) -> str:
    """The bullet in `rule` closest to `comment_body`, or the rule's filename.

    Called by both retro-scan's GitHub-comment path and `retro_reviews`'
    local-finding path, so it is published rather than kept private to either.
    """
    comment_kw = extract_keywords(comment_body)
    best_bullet = ""
    best_overlap = 0
    for bullet in rule.get("bullets", []):
        bullet_kw = extract_keywords(bullet)
        overlap = len(comment_kw & bullet_kw)
        if overlap > best_overlap:
            best_overlap = overlap
            best_bullet = bullet
    return best_bullet[:100] if best_bullet else rule["filename"]
