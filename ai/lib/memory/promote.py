"""One promotion scan.

It reports every registered repo's memory, the backed-up memories in the
workbench, and the workbench's rules, scripts, hooks and agents, for the
promote skill to weigh.

Not: the store, its keys or topic-file parsing (`core.memory`), where the
registry lives (`config.workbench_projects`), argument parsing or the
default workbench (`cli.promote_scan`, `core.workbench_paths`).
"""

# doc-group: platform

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import config.workbench_projects
import core.log
import core.memory
import core.trail


# The binary a user runs and the trail records, which is not this module's own
# name. Spelled out rather than derived, so the shim can be renamed only by
# changing the name in both places at once.
SCRIPT = "promote-scan"
LAST_PROMOTE_STAMP = "last-promote"

RULES_REL = Path("ai") / "guidelines" / "rules"
AGENTS_REL = Path("ai") / "claude" / "agents"
SETTINGS_REL = Path("ai") / "claude" / "settings.json"
BACKED_UP_MEMORY_REL = Path("ai") / "memory"
BIN_REL = Path("bin")

DATETIME_FMT = "%Y-%m-%d %H:%M"

BODY_PREVIEW_LENGTH = 500


def first_heading(path: Path) -> str:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _read_last_promote(repo_path: Path) -> str | None:
    """When this repo last promoted, read from the gate stamp.

    The stamp is regenerable state and sits under the gates root with the
    other cooldowns, rather than among the authored topic files.
    """
    stamp = core.memory.gate_stamp_file(repo_path, LAST_PROMOTE_STAMP)
    try:
        ts = int(stamp.read_text().strip())
    except (ValueError, OSError):
        return None
    return datetime.fromtimestamp(ts).strftime(DATETIME_FMT)


def _topic_row(tf: core.memory.TopicFile) -> dict:
    return {
        "filename": tf.filename,
        "name": tf.name,
        "description": tf.description,
        "type": tf.type,
        "body": (tf.body or "")[:BODY_PREVIEW_LENGTH],
        "modified": tf.modified,
        "stale": tf.stale,
        "age_days": tf.age_days,
    }


def scan_memory_state() -> list[dict]:
    """Every registered repo's memory, read forward from the registry.

    Forward rather than by globbing the memory root: the key is a truncated
    slug plus a digest, so a directory name cannot say which repo it is.
    """
    states = []
    for repo_path in config.workbench_projects.registered():
        try:
            directory = core.memory.memory_dir(repo_path)
        except ValueError:
            continue
        state = core.memory.state_of(directory, with_body=True)
        if state is None:
            continue
        states.append({
            "project_id": state.repo_key,
            "line_count": state.line_count,
            "last_promote": _read_last_promote(repo_path),
            "topic_files": [_topic_row(tf) for tf in state.topic_files],
        })
    return states


def scan_backed_up_memories(workbench: Path) -> list[dict]:
    memory_dir = workbench / BACKED_UP_MEMORY_REL
    if not memory_dir.exists():
        return []

    now = datetime.now()
    results = []
    for f in sorted(memory_dir.glob(core.memory.TOPIC_GLOB)):
        tf = core.memory.scan_topic_file(f, now, with_body=True)
        if tf is not None:
            results.append(_topic_row(tf))
    return results


def scan_rules(workbench: Path) -> list[dict]:
    rules_dir = workbench / RULES_REL
    if not rules_dir.exists():
        return []

    results = []
    for f in sorted(rules_dir.glob("*.md")):
        heading = first_heading(f)
        body = core.memory.read_body(f)
        results.append({
            "filename": f.name,
            "heading": heading,
            "body": body[:BODY_PREVIEW_LENGTH],
        })
    return results


def scan_scripts(workbench: Path) -> list[str]:
    bin_dir = workbench / BIN_REL
    if not bin_dir.exists():
        return []

    results = []
    for f in sorted(bin_dir.iterdir()):
        if f.is_file():
            results.append(f.name)
    return results


def scan_hooks(workbench: Path) -> list[dict]:
    settings_file = workbench / SETTINGS_REL
    if not settings_file.exists():
        return []

    try:
        data = json.loads(settings_file.read_text())
    except (json.JSONDecodeError, OSError):
        core.log.warn(f"Could not parse {settings_file}")
        return []

    hooks = data.get("hooks", {})
    results = []
    for event, hook_list in hooks.items():
        if not isinstance(hook_list, list):
            continue
        for hook in hook_list:
            matcher = hook.get("matcher", "")
            cmd = hook.get("command", "")
            results.append({
                "event": event,
                "matcher": matcher,
                "command": cmd[:100],
            })
    return results


def scan_agents(workbench: Path) -> list[dict]:
    agents_dir = workbench / AGENTS_REL
    if not agents_dir.exists():
        return []

    results = []
    for f in sorted(agents_dir.glob("*.md")):
        heading = first_heading(f)
        results.append({
            "filename": f.name,
            "heading": heading,
        })
    return results


def _format_topic_entry(tf: dict, heading_prefix: str) -> list[str]:
    lines: list[str] = []
    stale_marker = " **STALE**" if tf["stale"] else ""
    lines.append(f"{heading_prefix} {tf['name'] or tf['filename']} ({tf['type']}){stale_marker}")
    lines.append(f"*{tf['description']}* — {tf['filename']}, {tf['age_days']}d old")
    if tf["body"]:
        lines.append("")
        lines.append(tf["body"])
    lines.append("")
    return lines


def _format_memory_state(state: dict) -> list[str]:
    lines = [f"### {state['project_id']}"]
    lines.append(f"MEMORY.md: {state['line_count']} lines")
    if state["last_promote"]:
        lines.append(f"Last promote: {state['last_promote']}")
    lines.append("")

    if not state["topic_files"]:
        return lines

    for tf in state["topic_files"]:
        lines.extend(_format_topic_entry(tf, "####"))

    return lines


def _format_backed_up(entries: list[dict]) -> list[str]:
    if not entries:
        return ["No backed-up memories found.\n"]

    lines: list[str] = []
    for tf in entries:
        lines.extend(_format_topic_entry(tf, "###"))
    return lines


def _format_rule_body(body: str) -> list[str]:
    return ["", *(f"> {line}" for line in body.splitlines())]


def _format_rules(rules: list[dict]) -> list[str]:
    if not rules:
        return ["No rules found.\n"]
    lines: list[str] = []
    for r in rules:
        lines.append(f"#### `{r['filename']}` — {r['heading']}")
        if r.get("body"):
            lines.extend(_format_rule_body(r["body"]))
        lines.append("")
    return lines


def _format_scripts(scripts: list[str]) -> list[str]:
    if not scripts:
        return ["No scripts found.\n"]
    lines = [f"- `{s}`" for s in scripts]
    lines.append("")
    return lines


def _format_hooks(hooks: list[dict]) -> list[str]:
    if not hooks:
        return ["No hooks found.\n"]
    lines: list[str] = []
    for h in hooks:
        matcher_info = f" (matcher: `{h['matcher']}`)" if h["matcher"] else ""
        lines.append(f"- **{h['event']}**{matcher_info}: `{h['command']}`")
    lines.append("")
    return lines


def _format_agents(agents: list[dict]) -> list[str]:
    if not agents:
        return ["No agents found.\n"]
    lines: list[str] = []
    for a in agents:
        lines.append(f"- `{a['filename']}` — {a['heading']}")
    lines.append("")
    return lines


def format_report(
    memory_states: list[dict],
    backed_up: list[dict],
    rules: list[dict],
    scripts: list[str],
    hooks: list[dict],
    agents: list[dict],
) -> str:
    lines: list[str] = []

    lines.append("## Memory State\n")
    if not memory_states:
        lines.append("No memory directories found.\n")
    else:
        for state in memory_states:
            lines.extend(_format_memory_state(state))

    lines.append("## Backed-Up Memories\n")
    lines.extend(_format_backed_up(backed_up))

    lines.append("## Workbench Artifacts\n")

    lines.append("### Rules\n")
    lines.extend(_format_rules(rules))

    lines.append("### Scripts\n")
    lines.extend(_format_scripts(scripts))

    lines.append("### Hooks\n")
    lines.extend(_format_hooks(hooks))

    lines.append("### Agents\n")
    lines.extend(_format_agents(agents))

    return "\n".join(lines)


def _resolve_workbench(raw: str) -> Path:
    return Path(os.path.expanduser(raw)).resolve()


def run_scan(home: str, workbench: str, *, debug: bool = False) -> int:
    home_path = Path(os.path.expanduser(home))
    workbench_path = _resolve_workbench(workbench)
    trail = core.trail.Trail.start(
        script=SCRIPT,
        context={"workbench": workbench},
        debug=debug,
    )

    try:
        core.log.info(f"Home: {home_path}")
        core.log.info(f"Workbench: {workbench_path}")

        memory_states = scan_memory_state()
        core.log.info(f"Found {len(memory_states)} project(s) with memory")
        trail.info("scan_memories", f"found {len(memory_states)} memory files across {sum(len(s.get('topic_files', [])) for s in memory_states)} topics")

        backed_up = scan_backed_up_memories(workbench_path)
        core.log.info(f"Found {len(backed_up)} backed-up memory file(s)")
        trail.info("scan_backed_up", f"found {len(backed_up)} backed-up memory files")

        rules = scan_rules(workbench_path)
        scripts = scan_scripts(workbench_path)
        hooks = scan_hooks(workbench_path)
        agents = scan_agents(workbench_path)
        core.log.info(f"Artifacts: {len(rules)} rules, {len(scripts)} scripts, {len(hooks)} hooks, {len(agents)} agents")
        trail.info("scan_artifacts", f"found {len(rules)} rules, {len(scripts)} scripts, {len(hooks)} hooks, {len(agents)} agents")

        report = format_report(memory_states, backed_up, rules, scripts, hooks, agents)
        trail.info("generate_report", f"report generated, {len(report)} chars")
        print(report)
        return 0
    except Exception as exc:
        trail.error("unexpected_error", str(exc))
        raise
    finally:
        trail.finish()
