"""One scan of the retro.

Resolves the Project Registry to GitHub repos, fetches their merged-PR review
comments and the local self-reviews, cross-references both against the rule
files, and prints the report. With `--consume` it records what it read.

Not: fetching (`retro.github`), local-review parsing (`retro.reviews`),
matching (`retro.rules`), rendering (`retro.report`), the consume record
(`retro.consumed`), argument parsing (`cli.retro_scan`).
"""

# doc-group: platform

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

import core.log
import core.trail
import core.trail_query
import core.version
import core.workbench_paths
import gh.client
import git.client
import retro.consumed
import retro.github
import retro.report
import retro.reviews
import retro.rules
import review.dedup


# ── Constants ────────────────────────────────────────────────────────────────

# The binary a user runs and the trail records, which is not this module's own
# name. Spelled out rather than derived, so the shim can be renamed only by
# changing the name in both places at once.
SCRIPT = "retro-scan"

MACHINE_MD_REL = Path(".claude") / "machine" / "machine.md"

# The retro cooldown stamp, under the gates directory rather than in a harness's
# tree: the question it answers is about this machine, not about Claude Code.
# RETRO_STAMP_FILE in lib/constants.sh is the bash half, which retro-complete.sh
# writes and should-retro.sh reads.
LAST_RETRO_NAME = "last-retro"

REGISTRY_TABLE_PATTERN = re.compile(r"^\|\s*(\S+)\s*\|\s*(\S+)\s*\|")
REGISTRY_SEPARATOR = re.compile(r"^\|\s*[-]+")

SSH_REMOTE_PATTERN = re.compile(r"git@github\.com:(.+?)(?:\.git)?$")
HTTPS_REMOTE_PATTERN = re.compile(r"https://github\.com/(.+?)(?:\.git)?$")


# ── Project registry ────────────────────────────────────────────────────────

def parse_project_registry(machine_md_path: str) -> list[dict]:
    """Every project row in the registry, with each path made usable.

    The profile writes paths home-abbreviated (`~/git/maximum`), so expanding
    them belongs here rather than at each call site — a literal `~` reaches a
    subprocess `cwd` as a directory that does not exist, and the resulting
    failure is indistinguishable from the repo genuinely lacking what was
    asked of it.
    """
    path = Path(machine_md_path)
    if not path.exists():
        core.log.warn(f"Machine profile not found: {path}")
        return []
    lines = path.read_text().splitlines()
    has_table_header = any(
        REGISTRY_TABLE_PATTERN.match(line) and REGISTRY_TABLE_PATTERN.match(line).group(1) == "Project"
        for line in lines
    )
    results = []
    for line in lines:
        if REGISTRY_SEPARATOR.match(line):
            continue
        m = REGISTRY_TABLE_PATTERN.match(line)
        if m and m.group(1) != "Project":
            results.append({
                "name": m.group(1),
                "path": os.path.expanduser(m.group(2)),
            })
    if has_table_header and not results:
        core.log.warn(f"Registry table header found but no project rows in {path}")
    elif not has_table_header:
        core.log.warn(f"No Project Registry table in {path}")
    return results


def resolve_github_remote(remote_url: str) -> str | None:
    for pattern in (SSH_REMOTE_PATTERN, HTTPS_REMOTE_PATTERN):
        m = pattern.match(remote_url)
        if m:
            return m.group(1)
    return None


def get_repo_remote(repo_path: str) -> str | None:
    """The repo's origin URL, or None if it has none.

    A directory that is not there is reported as itself rather than folded
    into the no-remote answer. Both reach this function as the same
    `FileNotFoundError` — one for the `cwd`, one for git — and reading a
    missing checkout as a repo without a remote is what let a registry of
    unexpanded paths look like six remoteless repos.

    A timeout arrives as a failed result rather than an exception, so only the
    missing-binary case still needs catching.
    """
    if not Path(repo_path).is_dir():
        core.log.warn(f"Registry path does not exist: {repo_path}")
        return None
    try:
        result = git.client.run("remote", "get-url", "origin", cwd=repo_path)
    except FileNotFoundError:
        return None
    return result.stdout.strip() if result.ok else None

def _read_last_retro() -> int:
    stamp = core.workbench_paths.gates_dir() / LAST_RETRO_NAME
    if not stamp.exists():
        return 0
    try:
        return int(stamp.read_text().strip())
    except ValueError as e:
        core.log.warn(f"Invalid last-retro timestamp in {stamp}: {e}")
        return 0
    except OSError as e:
        core.log.warn(f"Cannot read last-retro stamp {stamp}: {e}")
        return 0


def _collect_github_entries(github_repos: list[dict]) -> list[tuple[str | None, set[str]]]:
    return [
        (c.get("path"), review.dedup.word_set(c["body"]))
        for repo in github_repos
        for pr in repo.get("prs", [])
        for c in pr.get("comments", [])
    ]


def _is_dup_comment(comment: dict, github_entries: list[tuple[str | None, set[str]]]) -> bool:
    c_words = review.dedup.word_set(comment["body"])
    return any(
        comment.get("path") == gh_path
        and review.dedup.jaccard(c_words, gh_words) >= review.dedup.DEDUP_THRESHOLD
        for gh_path, gh_words in github_entries
    )


def _dedup_pr_comments(pr: dict, github_entries: list[tuple[str | None, set[str]]]) -> dict | None:
    kept = [c for c in pr.get("comments", []) if not _is_dup_comment(c, github_entries)]
    return {**pr, "comments": kept} if kept else None


def _dedup_local_against_github(
    github_repos: list[dict], local_repos: list[dict],
) -> list[dict]:
    """Remove local review comments that duplicate GitHub comments."""
    github_entries = _collect_github_entries(github_repos)
    if not github_entries:
        return local_repos

    deduped_repos: list[dict] = []
    for repo in local_repos:
        deduped_prs = [_dedup_pr_comments(pr, github_entries) for pr in repo.get("prs", [])]
        deduped_prs = [pr for pr in deduped_prs if pr]
        if deduped_prs:
            deduped_repos.append({**repo, "prs": deduped_prs})
    return deduped_repos


def _parse_since(since: str) -> int:
    """The scan window *since* names, as the epoch seconds this scan compares against.

    The parsing is `core.trail_query`'s; this is the epoch form the GitHub
    queries downstream take. `strict` because a window is a scan parameter here
    rather than a glance: reading an unrecognized `--since` as the default hour
    would make the report claim there was nothing to find.
    """
    return int(core.trail_query.parse_since(since, strict=True).timestamp())


def _annotate_comment(
    comment: dict, pr_author: str, git_user: str,
    rules: list[dict], rule_match_counts: dict[str, dict],
    weights: retro.rules.TermWeights,
) -> str:
    """Returns "skip", "matched", or "unmatched"."""
    comment_author = comment.get("author", "unknown")
    if git_user and comment_author == git_user and pr_author == git_user:
        return "skip"
    if git_user and comment_author == git_user:
        comment["direction"] = "gave"
    elif git_user and pr_author == git_user:
        comment["direction"] = "received"
    else:
        comment["direction"] = "observed"
    nearest = retro.rules.find_nearest_rule(comment["body"], rules, weights)
    if nearest:
        comment["nearest_rule"] = {
            "filename": nearest["filename"],
            "match_snippet": retro.report.format_matched_snippet(
                comment["body"], nearest, weights,
            ),
        }
        rule_match_counts[nearest["filename"]]["matched"] += 1
        return "matched"
    comment["nearest_rule"] = None
    return "unmatched"


def _scan_repo(
    repo: dict, last_retro_ts: int, rules: list[dict],
    git_user: str, rule_match_counts: dict[str, dict],
    weights: retro.rules.TermWeights,
) -> dict:
    core.log.info(f"Fetching PRs for {repo['github']}...")
    pr_data = retro.github.fetch_repo_review_data(repo["github"], last_retro_ts)
    core.log.info(f"  Found {len(pr_data)} merged PR(s) with comments")
    if last_retro_ts == 0:
        core.log.info(f"  First run: fetched {len(pr_data)} PR(s) (limit: {retro.github.GQL_MERGED_PRS_LIMIT})")

    repo_prs: list[dict] = []
    unmatched = 0
    for pr in pr_data:
        pr_author = pr.get("user", {}).get("login", "")
        comments = pr["comments"]
        results = [
            _annotate_comment(
                c, pr_author, git_user, rules, rule_match_counts, weights,
            )
            for c in comments
        ]
        comments = [c for c, r in zip(comments, results) if r != "skip"]
        um = sum(1 for r in results if r == "unmatched")
        unmatched += um
        if comments:
            repo_prs.append({
                "number": pr["number"],
                "title": pr.get("title", ""),
                "author": pr_author,
                "merged_at": pr.get("merged_at", "")[:10],
                "comments": comments,
                "unmatched": um,
            })
    return {"prs": repo_prs, "unmatched": unmatched}


def _all_matched_comments(scan_repos: list[dict]) -> list[tuple[str, int, str]]:
    """Yield (rule_filename, pr_number, body) for every matched comment."""
    return [
        (c["nearest_rule"]["filename"], pr["number"], c["body"])
        for repo in scan_repos
        for pr in repo.get("prs", [])
        for c in pr.get("comments", [])
        if c.get("nearest_rule")
    ]


def _build_themes(scan_repos: list[dict]) -> dict[str, list[dict]]:
    """Group matched comments by nearest rule file for cross-PR pattern detection."""
    themes: dict[str, list[dict]] = {}
    for rule_file, pr_num, body in _all_matched_comments(scan_repos):
        themes.setdefault(rule_file, []).append({"pr": pr_num, "body": body})
    return themes


def _run_retro(
    trail, home, workbench, since_override: str | None = None, consume: bool = False,
) -> int:
    core.log.info(f"Home: {home}")
    core.log.info(f"Workbench: {workbench}")

    machine_md = home / MACHINE_MD_REL
    projects = parse_project_registry(str(machine_md))
    if projects:
        core.log.info(f"Found {len(projects)} project(s) in registry")
        trail.info("parse_registry", f"found {len(projects)} projects in {machine_md}")
    else:
        core.log.warn(f"Found 0 projects in {machine_md} (exists={machine_md.exists()})")
        trail.warn("parse_registry", f"found 0 projects in {machine_md} (exists={machine_md.exists()})")

    repos: list[dict] = []
    for proj in projects:
        remote_url = get_repo_remote(proj["path"])
        if not remote_url:
            core.log.warn(f"No git remote for {proj['name']} at {proj['path']}")
            continue
        github_slug = resolve_github_remote(remote_url)
        if not github_slug:
            core.log.warn(f"Non-GitHub remote for {proj['name']}: {remote_url}")
            continue
        repos.append({**proj, "github": github_slug})
    core.log.info(f"Resolved {len(repos)} GitHub repo(s)")
    skipped = len(projects) - len(repos)
    if repos:
        if skipped:
            trail.warn("resolve_remotes", f"resolved {len(repos)} GitHub repos from {len(projects)} projects ({skipped} skipped)")
        else:
            trail.info("resolve_remotes", f"resolved {len(repos)} GitHub repos from {len(projects)} projects")

    if not repos:
        # A registry that resolves to nothing is a broken scan, not an empty
        # one. Finishing the run would produce a report built only from local
        # self-review output, then bank it: retro-complete.sh writes the
        # cooldown stamp and deletes the review directories this run consumed,
        # so the window closes over PR feedback that was never read.
        # ceiling: no override for a machine whose repos are all non-GitHub --
        # such a machine cannot run retro at all. Add an opt-out flag if one
        # registers a repo on another forge.
        # Both arrive here as "no repos", but they are different faults with
        # different remedies, and the count alone reads as nonsense for the
        # first ("none of the 0 registered projects resolved").
        if projects:
            detail = (
                f"none of the {len(projects)} registered projects resolved to a GitHub repo "
                "— check the Project Registry paths in the machine profile"
            )
        else:
            detail = (
                f"the Project Registry in {machine_md} lists no projects "
                "— run `otto-workbench projects` to register one, or refresh the machine profile"
            )
        core.log.error(f"Refusing to bank a scan window over local reviews alone: {detail}.")
        trail.error("resolve_remotes", f"0 GitHub repos from {len(projects)} projects")
        return 1

    rules = retro.rules.load_rules(workbench)
    if rules:
        core.log.info(f"Loaded {len(rules)} rule file(s)")
        trail.info("load_rules", f"loaded {len(rules)} rule files from workbench")
    else:
        core.log.warn(f"No rule files found in {workbench / retro.rules.RULES_REL}")
        trail.warn("load_rules", f"no rule files in {workbench / retro.rules.RULES_REL}")

    if since_override:
        last_retro_ts = _parse_since(since_override)
        core.log.info(f"Scan window override: --since {since_override}")
    else:
        last_retro_ts = _read_last_retro()
    if last_retro_ts > 0:
        core.log.info(f"Last retro: {datetime.fromtimestamp(last_retro_ts).strftime(retro.report.DATE_FMT)}")
    else:
        core.log.info("No previous retro found — scanning all available PRs")
    trail.info("check_state", f"last retro timestamp: {last_retro_ts}")

    git_user = gh.client.login()
    if not git_user:
        trail.warn("github_login", "could not determine GitHub login — comment directions will be inaccurate")

    scan_repos: list[dict] = []
    rule_match_counts: dict[str, dict] = {r["filename"]: {"matched": 0} for r in rules}
    unmatched_count = 0
    # Once for the scan, not once per comment: the IDF is over the rule set,
    # and a scan scores thousands of comments against the one set.
    weights = retro.rules.term_weights(rules)

    for repo in repos:
        result = _scan_repo(
            repo, last_retro_ts, rules, git_user, rule_match_counts, weights,
        )
        unmatched_count += result["unmatched"]
        if result["prs"]:
            scan_repos.append({
                "github": repo["github"],
                "prs": result["prs"],
                "unmatched": result["unmatched"],
            })
    trail.info("fetch_pr_comments", f"fetched review data from {len(repos)} repos")

    local_scan = retro.reviews.scan_local_reviews(
        core.workbench_paths.reviews_dir(), rules, rule_match_counts, weights,
    )
    local_repos = _dedup_local_against_github(scan_repos, local_scan.repos)
    for lr in local_repos:
        lr["unmatched"] = sum(
            1 for pr in lr.get("prs", [])
            for c in pr.get("comments", [])
            if not c.get("nearest_rule")
        )
    scan_repos.extend(local_repos)
    unmatched_count += local_scan.unmatched
    trail.info("scan_local_reviews", f"scanned {sum(len(r['prs']) for r in local_repos)} local reviews")

    if consume:
        # Written even when the scan consumed nothing. The record is this
        # scan's claim over the reviews root, and an empty claim still has to
        # displace the previous run's — skipping the write on empty is what
        # lets an abandoned retro's list survive into a later completion.
        retro.consumed.write_record(retro.consumed.ConsumeRecord(
            scan_id=trail.root,
            scanned_at=datetime.now(timezone.utc).isoformat(),
            reviews=local_scan.consumed,
        ))
        trail.info("write_consumed", f"recorded {len(local_scan.consumed)} consumed review dirs under scan {trail.root}")

    themes = _build_themes(scan_repos)

    rules_summary = []
    for r in rules:
        rc = rule_match_counts[r["filename"]]
        rules_summary.append({
            "filename": r["filename"],
            "matched": rc["matched"],
        })
    if unmatched_count:
        rules_summary.append({"filename": "(no rule)", "matched": unmatched_count})
    trail.info("cross_reference_rules", f"cross-referenced {sum(rc['matched'] for rc in rule_match_counts.values())} comments against rules")

    scan_data = {
        "repos": scan_repos,
        "rules_summary": rules_summary,
        "themes": themes,
    }
    version = core.version.version_string(SCRIPT).split("\n")[0]
    report = retro.report.format_report(scan_data, version)
    trail.info("generate_report", f"report generated, {len(report)} chars")
    print(report)
    if consume:
        # The handle the completion quotes back. Printed after the report so it
        # is the last thing on stdout whatever the report's own length, and as a
        # comment so a reader pasting the report somewhere does not see it.
        print(f"\n<!-- scan-id: {trail.root} | consumed: {len(local_scan.consumed)} -->")
    return 0


def run_scan(
    home: str,
    workbench: str,
    *,
    since: str | None = None,
    consume: bool = False,
    debug: bool = False,
) -> int:
    home_path = Path(os.path.expanduser(home))
    workbench_path = Path(os.path.expanduser(workbench)).resolve()
    trail = core.trail.Trail.start(
        script=SCRIPT,
        context={"workbench": workbench, "home": str(home_path)},
        debug=debug,
    )
    try:
        return _run_retro(
            trail, home_path, workbench_path,
            since_override=since, consume=consume,
        )
    except Exception as exc:
        trail.error("unexpected_error", str(exc))
        raise
    finally:
        trail.finish()
