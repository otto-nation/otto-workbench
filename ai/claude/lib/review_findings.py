"""Finding parsing, renumbering, deduplication, verification, and stable IDs.

Shared between review-orchestrate (merging/verification) and review-post (parsing).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from review_common import (
    SEVERITIES, SEVERITY_MUST, SEVERITY_SHOULD,
    SECTION_FILE_TRIAGE,
    _info, _warn,
)

_FINDING_SECTIONS = [(s.section, s.key) for s in SEVERITIES]

# Severity header name -> key mapping (section + aliases, lowercased)
_SEVERITY_NAMES: dict[str, str] = {}
for _s in SEVERITIES:
    _SEVERITY_NAMES[_s.section.lower()] = _s.key
    _SEVERITY_NAMES[_s.section.lower().replace("-", " ")] = _s.key
    for _alias in _s.aliases:
        _SEVERITY_NAMES[_alias.lower()] = _s.key


# ── Finding dataclass ────────────────────────────────────────────────────────

@dataclass
class Finding:
    id: str
    severity: str
    seq: int
    path: str
    line: int | None
    end_line: int | None
    body: str
    full_path: str = ""
    posted_id: str = ""
    classification: str = ""
    skip_reason: str = ""


# ── Finding parsing ──────────────────────────────────────────────────────────

FINDING_ID_RE = re.compile(
    r"^- (?:\[ \] )?"
    r"(?:~~)?"
    r"\*\*\[([MSNI])(\d+)\](?:\*\*)?"
    r"\s+"
    r"(?:<!-- sid:\w+ -->\s+)?"
)

PATH_SECTION_RE = re.compile(
    r"\*\*`(.+?)`\*\*"
    r"|"
    r"\*\*(.+?)\*\*"
    r"|"
    r"`(.+?)`"
)
FIRST_FILE_RE = re.compile(
    r"([a-zA-Z0-9_/().:-]+\.\w+)"
    r"(?::(\d+)(?:[-–](\d+))?)?"
)

STRIKETHROUGH_RE = re.compile(r"^- ~~\*\*\[")


def _extract_path(after_id: str) -> tuple[str, int | None, int | None]:
    section_match = PATH_SECTION_RE.match(after_id)
    if not section_match:
        return "", None, None
    section_text = section_match.group(1) or section_match.group(2) or section_match.group(3) or ""
    section_text = section_text.replace("\\_", "_")
    file_match = FIRST_FILE_RE.match(section_text)
    if not file_match:
        return "", None, None
    path = file_match.group(1).strip()
    line_num = int(file_match.group(2)) if file_match.group(2) else None
    end_line = int(file_match.group(3)) if file_match.group(3) else None
    return path, line_num, end_line


def _extract_body_text(line: str) -> str:
    em_dash_pos = line.find("—")
    if em_dash_pos != -1:
        return line[em_dash_pos + 1:].strip()
    for sep in (" -- ", " - "):
        pos = line.find(sep)
        if pos != -1:
            return line[pos + len(sep):].strip()
    return ""


def _parse_finding_line(stripped: str) -> Finding | None:
    id_match = FINDING_ID_RE.match(stripped)
    if not id_match:
        return None
    sev = id_match.group(1)
    seq = int(id_match.group(2))
    after_id = stripped[id_match.end():]
    path, line_num, end_line = _extract_path(after_id)
    if path:
        body = _extract_body_text(stripped)
    else:
        body = after_id.strip()
    return Finding(
        id=f"{sev}{seq}", severity=sev, seq=seq,
        path=path, line=line_num, end_line=end_line, body=body,
    )


def _finalize_finding(finding: Finding, body_lines: list[str]):
    body = "\n".join(body_lines).strip()
    if not body:
        return
    body = re.sub(r"\n+###[^\n]*$", "", body, flags=re.DOTALL).strip()
    finding.body = body


def _close_previous(findings: list[Finding], body_lines: list[str]):
    if findings:
        _finalize_finding(findings[-1], body_lines)
    body_lines.clear()


def _is_section_boundary(stripped: str) -> bool:
    return stripped.startswith("### ") or bool(STRIKETHROUGH_RE.match(stripped))


def _match_severity_header(stripped: str) -> str | None:
    if not stripped.startswith("#"):
        return None
    text = stripped.lstrip("#").strip().lower().replace("-", " ")
    return _SEVERITY_NAMES.get(text)


def parse_findings(text: str) -> list[Finding]:
    findings: list[Finding] = []
    current_severity: str | None = None
    body_lines: list[str] = []
    accumulating = False

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        severity = _match_severity_header(stripped)
        if severity is not None:
            _close_previous(findings, body_lines)
            current_severity = severity
            accumulating = False
            continue
        if stripped.startswith("## "):
            _close_previous(findings, body_lines)
            current_severity = None
            accumulating = False
            continue
        if current_severity is None:
            continue
        if _is_section_boundary(stripped):
            _close_previous(findings, body_lines)
            accumulating = False
            continue
        finding = _parse_finding_line(stripped)
        if finding:
            _close_previous(findings, body_lines)
            body_lines = [finding.body] if finding.body else []
            findings.append(finding)
            accumulating = True
            continue
        if accumulating and stripped:
            body_lines.append(raw_line.rstrip())

    _close_previous(findings, body_lines)
    return findings


# ── Diff hunk parsing ────────────────────────────────────────────────────────

_DIFF_FILE_RE = re.compile(r"^\+\+\+ b/(.+)")
_DIFF_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_diff_hunks(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    hunks: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None
    for line in diff_text.splitlines():
        m = _DIFF_FILE_RE.match(line)
        if m:
            current_file = m.group(1)
            hunks.setdefault(current_file, [])
            continue
        m = _DIFF_HUNK_RE.match(line)
        if m and current_file is not None:
            start = int(m.group(1))
            length = int(m.group(2)) if m.group(2) is not None else 1
            hunks[current_file].append((start, start + length - 1))
    return hunks


# ── Section extraction ───────────────────────────────────────────────────────

def _extract_section(content: str, header: str) -> str:
    pattern = rf"^## {re.escape(header)}\s*\n"
    m = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ""
    start = m.end()
    next_header = re.search(r"^## ", content[start:], re.MULTILINE)
    end = start + next_header.start() if next_header else len(content)
    return content[start:end].strip()


_EMPTY_SECTION_LINE_RE = re.compile(
    r"^(?:_none\._|_\(none\)_|_none in this file group\._|---)\s*$",
    re.IGNORECASE,
)


def _clean_section_text(text: str) -> str:
    lines = [line for line in text.split("\n") if not _EMPTY_SECTION_LINE_RE.match(line.strip())]
    return "\n".join(lines).strip()


_VALID_SECTION_HEADERS = {s.section.lower() for s in SEVERITIES} | {SECTION_FILE_TRIAGE.lower()}


def _validate_group_output(output_path: str, group_name: str) -> bool:
    content = Path(output_path).read_text()
    if not content.strip():
        return True
    has_section = any(
        line.strip()[3:].strip().lower() in _VALID_SECTION_HEADERS
        for line in content.split("\n")
        if line.strip().startswith("## ")
    )
    if not has_section:
        _warn(f"Group {group_name} output has no recognized sections — findings may be lost")
    return has_section


# ── Renumbering ──────────────────────────────────────────────────────────────

def renumber_section(prefix: str, text: str, offset: int) -> tuple[str, int]:
    if not text:
        return "", 0
    count = len(set(re.findall(rf"\[{prefix}\d+\]", text)))
    if offset > 0:
        for i in range(count, 0, -1):
            text = text.replace(f"[{prefix}{i}]", f"[{prefix}{i + offset}]")
    return text, count


def _renumber_prefix(text: str, prefix: str) -> str:
    seen_ids: list[int] = []
    for m in re.finditer(rf"\[{prefix}(\d+)\]", text):
        num = int(m.group(1))
        if num not in seen_ids:
            seen_ids.append(num)

    remap = {}
    for new_num, old_num in enumerate(seen_ids, 1):
        if old_num != new_num:
            remap[old_num] = new_num

    if not remap:
        return text

    placeholder = "\x00"
    for old_num in sorted(remap, reverse=True):
        text = text.replace(f"[{prefix}{old_num}]", f"[{placeholder}{prefix}{remap[old_num]}]")
    text = text.replace(placeholder, "")

    for old_num in sorted(remap, reverse=True):
        text = re.sub(
            rf"(?<!\[)(?<!\x00){prefix}{old_num}(?!\d)(?!\])",
            f"\x00{prefix}{remap[old_num]}",
            text,
        )
    return text.replace("\x00", "")


def renumber_findings(review_file: str) -> None:
    path = Path(review_file)
    if not path.exists():
        return
    text = path.read_text()
    for _, prefix in _FINDING_SECTIONS:
        text = _renumber_prefix(text, prefix)
    path.write_text(text)


# ── Triage and deduplication ─────────────────────────────────────────────────

_TRIAGE_LINE_RE = re.compile(r"^- `[^`]+`\s")


def _clean_triage(text: str) -> str:
    return "\n".join(
        line for line in text.split("\n")
        if _TRIAGE_LINE_RE.match(line)
    )


_FINDING_PATH_RE = re.compile(
    r"^- (?:\[ \] )?\*\*\[\w+\d+\]\*\*"
    r"\s+(?:<!-- sid:\w+ -->\s+)?"
    r"\*\*(?:`([^`]+)`|([^*]+))\*\*"
)
_FINDING_DESC_RE = re.compile(r"—\s*(.{0,80})")


def _finding_dedup_key(line: str) -> tuple[str, str] | None:
    m = _FINDING_PATH_RE.match(line)
    if not m:
        return None
    path = (m.group(1) or m.group(2) or "").replace("\\_", "_").strip()
    dm = _FINDING_DESC_RE.search(line)
    desc = dm.group(1).strip().lower() if dm else ""
    return (path, desc)


def _dedup_findings(text: str, prefix: str) -> str:
    seen: set[tuple[str, str]] = set()
    kept: list[str] = []
    skipping = False
    for line in text.split("\n"):
        key = _finding_dedup_key(line)
        if key is not None and key in seen:
            skipping = True
            continue
        if key is not None:
            seen.add(key)
            skipping = False
        if skipping:
            continue
        kept.append(line)
    return _renumber_prefix("\n".join(kept), prefix)


def _merge_one_review(
    content: str, merged_triage: str,
    merged: dict[str, str], offsets: dict[str, int],
) -> str:
    triage = _extract_section(content, SECTION_FILE_TRIAGE)
    if triage:
        cleaned = _clean_triage(triage)
        if cleaned:
            merged_triage += cleaned + "\n"
    for section, prefix in _FINDING_SECTIONS:
        raw = _clean_section_text(_extract_section(content, section))
        text, count = renumber_section(prefix, raw, offsets[section])
        if text:
            merged[section] += text + "\n"
        offsets[section] += count
    return merged_triage


def _dedup_triage(triage: str) -> str:
    seen: set[str] = set()
    lines = []
    for line in triage.split("\n"):
        m = re.match(r"^- `([^`]+)`", line)
        key = m.group(1) if m else line
        if key not in seen:
            seen.add(key)
            lines.append(line)
    return "\n".join(lines)


def merge_reviews(group_files: list[str]) -> str:
    merged_triage = ""
    merged: dict[str, str] = {section: "" for section, _ in _FINDING_SECTIONS}
    offsets: dict[str, int] = {section: 0 for section, _ in _FINDING_SECTIONS}

    for path in group_files:
        p = Path(path)
        if not p.exists():
            continue
        merged_triage = _merge_one_review(p.read_text(), merged_triage, merged, offsets)

    merged_triage = _dedup_triage(merged_triage)

    for section, prefix in _FINDING_SECTIONS:
        if merged[section]:
            merged[section] = _dedup_findings(merged[section], prefix)

    parts = [f"## {SECTION_FILE_TRIAGE}\n{merged_triage}"]
    for section, _ in _FINDING_SECTIONS:
        if merged[section]:
            parts.append(f"## {section}\n{merged[section]}")
    return "\n".join(parts)


# ── Counting ─────────────────────────────────────────────────────────────────

def _count_findings(text: str) -> dict[str, int]:
    return {
        prefix: len(set(re.findall(
            rf"^- (?:\[ \] )?\*\*\[{prefix}(\d+)\]\*\*",
            text,
            re.MULTILINE,
        )))
        for _, prefix in _FINDING_SECTIONS
    }


def _has_findings(merged_content: str) -> bool:
    for _, prefix in _FINDING_SECTIONS:
        if re.search(rf"\[{prefix}\d+\]", merged_content):
            return True
    return False


# ── Evidence verification ────────────────────────────────────────────────────

_EVIDENCE_RE = re.compile(
    r">\s*```\w*\n(.*?)\n\s*>\s*```",
    re.DOTALL,
)


def _extract_evidence(body: str) -> str | None:
    m = _EVIDENCE_RE.search(body)
    if not m:
        return None
    lines = m.group(1).split("\n")
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("> "):
            stripped = stripped[2:]
        elif stripped.startswith(">"):
            stripped = stripped[1:]
        cleaned.append(stripped.rstrip())
    return "\n".join(ln for ln in cleaned if ln)


def _normalize_code(text: str) -> str:
    lines = text.strip().split("\n")
    return "\n".join(line.strip() for line in lines if line.strip())


def _verify_finding(path: str, evidence: str | None, wt_path: str) -> bool:
    resolved = Path(wt_path) / path
    if not resolved.exists():
        return False
    if evidence is None:
        return True
    try:
        file_content = resolved.read_text()
    except OSError:
        return False
    return _normalize_code(evidence) in _normalize_code(file_content)


_VERIFY_FINDING_RE = re.compile(
    r"^- (?:\[ \] )?"
    r"\*\*\[([MSNI])(\d+)\]\*\*"
    r"\s+(?:<!-- sid:\w+ -->\s+)?"
    r"(?:\*\*[`]?([^`*\s]+?)[`]?\*\*"
    r"|[`]([^`\s]+?)[`])"
    r"(?::\d+(?:[-–]\d+)?)?"
    r"\s*—\s*(.*)"
)


def _flush_finding(findings: list[dict], current: dict | None, body_lines: list[str]) -> None:
    if not current:
        return
    current["body"] = "\n".join(body_lines)
    findings.append(current)


def _parse_findings_for_verification(text: str) -> list[dict]:
    findings: list[dict] = []
    current: dict | None = None
    body_lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        m = _VERIFY_FINDING_RE.match(stripped)
        if m:
            _flush_finding(findings, current, body_lines)
            raw_path = (m.group(3) or m.group(4) or "").replace("\\_", "_")
            path_str = raw_path.rsplit(":", 1)[0] if ":" in raw_path else raw_path
            current = {
                "id": f"{m.group(1)}{m.group(2)}",
                "severity": m.group(1),
                "path": path_str,
                "body": m.group(5),
            }
            body_lines = [m.group(5)] if m.group(5) else []
        elif current and stripped and not stripped.startswith("## "):
            body_lines.append(line.rstrip())
        elif current and stripped.startswith("## "):
            _flush_finding(findings, current, body_lines)
            current = None
            body_lines = []
    _flush_finding(findings, current, body_lines)
    return findings


def _is_next_finding_or_section(stripped: str) -> bool:
    return (
        stripped.startswith("- **[")
        or stripped.startswith("- [ ] **[")
        or stripped.startswith("## ")
    )


def _remove_dropped_findings(text: str, dropped: list[str]) -> str:
    dropped_set = set(dropped)
    kept: list[str] = []
    skip_until_next = False
    for line in text.split("\n"):
        stripped = line.strip()
        m = _VERIFY_FINDING_RE.match(stripped)
        if m:
            skip_until_next = f"{m.group(1)}{m.group(2)}" in dropped_set
        elif skip_until_next and _is_next_finding_or_section(stripped):
            skip_until_next = False
        if skip_until_next:
            continue
        kept.append(line)
    return "\n".join(kept)


def verify_findings(review_file: str, wt_path: str) -> list[str]:
    path = Path(review_file)
    if not path.exists():
        return []
    text = path.read_text()
    findings = _parse_findings_for_verification(text)
    dropped: list[str] = []
    for f in findings:
        if f["severity"] not in (SEVERITY_MUST, SEVERITY_SHOULD):
            continue
        evidence = _extract_evidence(f["body"])
        if not _verify_finding(f["path"], evidence, wt_path):
            dropped.append(f["id"])
    if not dropped:
        return []
    path.write_text(_remove_dropped_findings(text, dropped))
    return dropped


_EVIDENCE_BLOCK_START_RE = re.compile(r"^ {2,}>\s*```")


def strip_evidence_blocks(review_file: str) -> None:
    path = Path(review_file)
    if not path.exists():
        return
    lines = path.read_text().split("\n")
    kept: list[str] = []
    in_evidence = False
    for line in lines:
        is_fence = _EVIDENCE_BLOCK_START_RE.match(line)
        if not in_evidence and is_fence:
            in_evidence = True
            continue
        if in_evidence:
            in_evidence = not is_fence
            continue
        kept.append(line)
    path.write_text("\n".join(kept))


# ── Stable IDs for carry-forward ─────────────────────────────────────────────

def compute_stable_id(path: str, desc: str) -> str:
    key = f"{path.strip().lower()}:{desc[:80].strip().lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:8]


_ANNOTATE_FINDING_RE = re.compile(
    r"^(- (?:\[ \] )?\*\*\[[A-Z]\d+\]\*\*)\s+"
)


def _extract_finding_path(line: str, after: str) -> str:
    path_m = _FINDING_PATH_RE.match(line)
    if path_m:
        path_str = (path_m.group(1) or path_m.group(2) or "").replace("\\_", "_").strip()
        return path_str.rsplit(":", 1)[0] if ":" in path_str else path_str
    cb_path_re = re.match(r"[`]?(\S+?)[`]?:\d+", after)
    return cb_path_re.group(1) if cb_path_re else ""


def _annotate_finding_line(line: str, m: re.Match) -> str:
    prefix = m.group(1)
    after = line[m.end():]
    path_str = _extract_finding_path(line, after)
    if not path_str:
        return line
    desc_m = _FINDING_DESC_RE.search(line)
    desc = desc_m.group(1).strip() if desc_m else ""
    sid = compute_stable_id(path_str, desc)
    return f"{prefix} <!-- sid:{sid} --> {after}"


def annotate_prior_with_stable_ids(review_text: str) -> str:
    lines = review_text.split("\n")
    result: list[str] = []
    for line in lines:
        m = _ANNOTATE_FINDING_RE.match(line)
        if m and "<!-- sid:" not in line:
            result.append(_annotate_finding_line(line, m))
        else:
            result.append(line)
    return "\n".join(result)


def _check_orphaned_prior_ids(prior_text: str, review_text: str) -> list[str]:
    prior_ids = set(re.findall(r"<!-- sid:(\w+) -->", prior_text))
    review_ids = set(re.findall(r"<!-- sid:(\w+) -->", review_text))
    return sorted(prior_ids - review_ids)


def strip_stable_ids(review_file: str) -> None:
    path = Path(review_file)
    if not path.exists():
        return
    text = path.read_text()
    cleaned = re.sub(r" <!-- sid:\w+ -->", "", text)
    if cleaned != text:
        path.write_text(cleaned)


# ── Mechanical verdict and review assembly ──────────────────────────────────

_VERDICT_LABELS = [(s.key, s.label) for s in SEVERITIES]

_MECHANICAL_NOTE = "(mechanically merged, not synthesized)"


def mechanical_verdict(counts: dict[str, int]) -> str:
    parts = [f"{counts[key]} {label}" for key, label in _VERDICT_LABELS if counts.get(key)]
    must = counts.get(SEVERITY_MUST, 0)
    should = counts.get(SEVERITY_SHOULD, 0)

    if must:
        action = "Request changes"
    elif should:
        action = "Needs discussion"
    elif parts:
        action = "Approve"
    else:
        return f"Approve — no findings {_MECHANICAL_NOTE}.\n"

    suffix = " only" if not must and not should else ""
    return f"{action} — {', '.join(parts)}{suffix} {_MECHANICAL_NOTE}.\n"


def post_process_findings(
    review_file: str,
    wt_path: str = "",
    prior_review: str = "",
) -> None:
    if prior_review:
        orphaned = _check_orphaned_prior_ids(
            annotate_prior_with_stable_ids(prior_review),
            Path(review_file).read_text(),
        )
        if orphaned:
            _warn(f"{len(orphaned)} prior findings neither carried forward nor resolved: {', '.join(orphaned)}")
    if wt_path:
        dropped = verify_findings(review_file, wt_path)
        if dropped:
            _info(f"Dropped {len(dropped)} unverified findings: {', '.join(dropped)}")
    strip_evidence_blocks(review_file)
    strip_stable_ids(review_file)
    renumber_findings(review_file)


def build_mechanical_review(
    merged_content: str,
    *,
    title: str,
    meta_header: str,
    group_count: int,
    summary_note: str,
    include_verdict: bool = True,
    file_count: int = 0,
) -> str:
    counts = _count_findings(merged_content)
    total = sum(counts.values())
    count_summary = f"{total} finding{'s' if total != 1 else ''}" if total else "No findings"

    if file_count:
        scope = f"across {file_count} file{'s' if file_count != 1 else ''} in {group_count} groups"
    else:
        scope = f"across {group_count} groups"

    summary = (
        f"{title}\n{meta_header}\n"
        f"## Summary\n"
        f"{count_summary} {scope}. "
        f"{summary_note}\n\n"
        f"{merged_content}\n"
    )
    if not include_verdict:
        return summary
    return f"{summary}\n## Verdict\n{mechanical_verdict(counts)}"
