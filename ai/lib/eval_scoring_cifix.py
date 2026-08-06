"""The ci-fix eval task: hand a failing repo to the fix agent, re-run the check.

There is no finding-matching here — the verify command is the oracle. A case is
either fixed or it is not, so what varies between models is the token cost of
getting there, which is the thing the CI ratchet gates on.

Each case ships a `reference-fix/` overlay: the same relative paths, already
corrected. It is not used at eval time; it exists so the test suite can prove the
case is solvable and the oracle is not vacuous, without spending a token.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import ai_backend
import ai_usage
from eval_scoring import ScoringResult
from eval_task import RunArtifacts, RunOptions, clean_env, create_temp_repo

FIX_MAX_TURNS = 30
FIX_MAX_BUDGET = 2.0

_DEFAULT_VERIFY = ["bash", "verify.sh"]

_PROMPT = """A check is failing in the repository at {repo_dir}.

Command: {command}
Exit code: {exit_code}

Output:
{output}

Fix the source so the command passes. Do not edit the command itself, and do not
weaken the check — the failure is real and the check is correct. Work only inside
{repo_dir}."""


def verify_command(manifest: dict) -> list[str]:
    """The command whose exit code decides the case."""
    return manifest.get("verify") or list(_DEFAULT_VERIFY)


def run_verify(repo_dir: str, manifest: dict, timeout: int) -> tuple[int, str]:
    """Run the case's check. A timeout counts as a failure, with a reason."""
    try:
        result = subprocess.run(
            verify_command(manifest),
            cwd=repo_dir, capture_output=True, text=True,
            timeout=timeout, env=clean_env(),
        )
    except subprocess.TimeoutExpired:
        return 1, "verify: timed out"
    except OSError as e:
        return 1, f"verify: could not run: {e}"
    return result.returncode, (result.stdout + result.stderr).strip()


def _unfixable(repo_dir: str, log_dir: str, output: str) -> RunArtifacts:
    """A case whose check already passes proves nothing — do not pay an agent for it.

    The zero usage here is real, not a placeholder: no agent was invoked.
    """
    return RunArtifacts(
        exit_code=1,
        temp_dirs=[repo_dir, log_dir],
        data={
            "fixed": False,
            "fixture_ok": False,
            "summary": "fixture does not fail before the fix",
            "verify_output": output,
        },
    )


class CiFixTask:
    """Fix a failing check in a throwaway repo, then re-run the check."""

    name = "ci-fix"

    def run(self, case_dir: Path, opts: RunOptions) -> RunArtifacts:
        manifest = json.loads((case_dir / "manifest.json").read_text())
        repo_dir = create_temp_repo(str(case_dir / "src"), prefix="eval-cifix-")
        log_dir = tempfile.mkdtemp(prefix="eval-cifix-log-")
        session_log = str(Path(log_dir) / "session.jsonl")

        pre_code, pre_output = run_verify(repo_dir, manifest, opts.timeout)
        if pre_code == 0:
            return _unfixable(repo_dir, log_dir, pre_output)

        rc = ai_backend.invoke_fix(
            _PROMPT.format(
                repo_dir=repo_dir,
                command=" ".join(verify_command(manifest)),
                exit_code=pre_code,
                output=pre_output,
            ),
            session_log=session_log,
            add_dirs=[repo_dir],
            max_turns=FIX_MAX_TURNS,
            max_budget=FIX_MAX_BUDGET,
            model=opts.model or None,
            task="eval-ci-fix",
            repo="eval/corpus",
        )

        post_code, post_output = run_verify(repo_dir, manifest, opts.timeout)
        return RunArtifacts(
            exit_code=rc,
            usage=ai_usage.parse_session_log(session_log),
            temp_dirs=[repo_dir, log_dir],
            data={
                "fixed": post_code == 0,
                "fixture_ok": True,
                "summary": "fixed" if post_code == 0 else "still failing",
                "verify_output": post_output,
            },
        )

    def score(self, artifacts: RunArtifacts, manifest: dict) -> ScoringResult:
        # Binary: the check passed or it did not. Severity accuracy has no meaning
        # for this task and stays at its zero default rather than being invented.
        passed = 1.0 if artifacts.data.get("fixed") else 0.0
        usage = artifacts.usage
        return ScoringResult(
            entry_name="", model="", run_index=0,
            recall=passed,
            precision=passed,
            cost_usd=usage.cost,
            duration_ms=usage.duration_ms,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            billed_input=usage.billed_input,
            cache_read_ratio=usage.cache_read_ratio,
        )
