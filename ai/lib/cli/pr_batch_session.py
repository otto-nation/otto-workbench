"""`pr batch session` and `pr batch note`: open a run's pi session, record a verdict in it.

Beside `cli.pr_batch` so the dispatcher stays under the file-size cap. What a
session is and what a note checks live in `batch.run_session`; this module
parses, prints and maps exits.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os
import sys

import batch.run_session
import batch.store
import cli.pr_batch_common
import cli.pr_batch_requests
import core.report
from batch.model import Verdict
from core.exit_codes import ExitCode


def add_session_parser(sub) -> None:
    s = sub.add_parser("session", help="Open a run's pi session, or print how to (--json)")
    s.add_argument("run_id", help="Run whose session to open")
    s.add_argument("--json", action="store_true",
                   help="Print {schema_version, session_id, name, cwd, argv, env} instead "
                        "of starting pi; UIs open the session from this, never their own argv")


def cmd_session(args) -> int:
    try:
        run = batch.store.load(args.run_id)
    except batch.store.RunNotFound:
        return cli.pr_batch_common.fail(f"no run {args.run_id}")
    try:
        recipe = batch.run_session.recipe(run)
    except batch.run_session.RunSessionError as exc:
        return cli.pr_batch_common.fail(str(exc))
    if args.json:
        core.report.emit_json(recipe.to_dict())
        return int(ExitCode.OK)
    return _exec(recipe)


def _exec(recipe: batch.run_session.SessionRecipe) -> int:
    if not os.path.isdir(recipe.cwd):
        return cli.pr_batch_common.fail(f"checkout {recipe.cwd} does not exist")
    sys.stdout.flush()
    sys.stderr.flush()
    os.chdir(recipe.cwd)
    try:
        # Replaces this process. `pr` records no trail for batch (it resolves
        # nothing and holds no lock), so nothing is left unfinished by not returning.
        os.execvpe(recipe.argv[0], list(recipe.argv), {**os.environ, **recipe.env})
    except OSError as exc:
        return cli.pr_batch_common.fail(f"could not start {recipe.argv[0]}: {exc}")


def _finding(text: str) -> str:
    reason, sep, step = text.partition(":")
    if not (reason and sep and step):
        raise argparse.ArgumentTypeError(f"--finding wants REASON:STEP, got {text!r}")
    return text


def add_note_parser(sub) -> None:
    n = sub.add_parser("note", help="Record a verdict on one finding in the run's history; "
                                    "changes no item state")
    n.add_argument("run_id", help="Run holding the PR")
    n.add_argument("pr", help="Item key owner/repo#N, or a PR number unique in the run")
    n.add_argument("--finding", type=_finding, required=True, metavar="REASON:STEP",
                   help="The finding as the report names it: its reason and step")
    n.add_argument("--verdict", choices=[v.value for v in Verdict], required=True,
                   help="What was decided")
    n.add_argument("--reason", required=True, help="Why, in a sentence; must not be empty")
    n.add_argument("--command", dest="commands", action="append", default=[], metavar="CMD",
                   help="A command run to act on it, as typed; repeatable")


def _usage(message: str) -> int:
    print(f"pr batch: {message}", file=sys.stderr)
    return int(ExitCode.USAGE)


def cmd_note(args) -> int:
    try:
        run = batch.store.load(args.run_id)
    except batch.store.RunNotFound:
        return _usage(f"no run {args.run_id}")
    item = cli.pr_batch_requests.resolve_item(run, args.pr)
    if isinstance(item, str):
        return _usage(item)
    try:
        rec = batch.run_session.note(run, item, args.finding, Verdict(args.verdict),
                                     args.reason, args.commands)
    except batch.run_session.RunSessionError as exc:
        return _usage(str(exc))
    except OSError as exc:
        return cli.pr_batch_common.fail(f"could not record the note in run {run.id}: {exc}")
    core.report.emit_json(rec)
    return int(ExitCode.OK)
