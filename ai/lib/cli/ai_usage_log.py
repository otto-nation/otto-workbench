"""Bridge shell-invoked AI calls into the global usage ledger.

Python callers go through ai_backend, which records usage itself. The shell caller
that cannot is run-auto-task: it needs slash commands, which ai_backend disables,
so it reaches the ledger through this tool instead.

  render   stdin JSONL -> readable stdout, raw stream teed to a file
  unwrap   stdin --output-format json envelope -> reply text, raw teed to a file
  record   parse a teed file and append one ledger record
"""

# doc-group: cli

from __future__ import annotations

import argparse

import agent.usage_log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render", help="stream-json on stdin to readable stdout")
    p_render.add_argument("--tee", help="write the raw stream to this path")

    p_unwrap = sub.add_parser("unwrap", help="JSON envelope on stdin to reply text")
    p_unwrap.add_argument("--tee", help="write the raw response to this path")

    p_record = sub.add_parser("record", help="append a ledger record from a teed file")
    p_record.add_argument("--from-log", required=True, help="teed raw output to parse")
    p_record.add_argument("--script", required=True, help="calling script name")
    p_record.add_argument("--entry-point", required=True, help="prompt, agent, or fix")
    p_record.add_argument("--backend", default="claude")
    p_record.add_argument("--model", default=None)
    p_record.add_argument("--exit-code", type=int, default=0)
    p_record.add_argument("--task", default=None)
    p_record.add_argument("--repo", default=None)
    p_record.add_argument("--pr", default=None)

    args = parser.parse_args(argv)
    if args.command == "render":
        return agent.usage_log.render(args.tee)
    if args.command == "unwrap":
        return agent.usage_log.unwrap(args.tee)
    return agent.usage_log.record_from_log(
        args.from_log, script=args.script, entry_point=args.entry_point,
        backend=args.backend, model=args.model, exit_code=args.exit_code,
        task=args.task, repo=args.repo, pr=args.pr,
    )
