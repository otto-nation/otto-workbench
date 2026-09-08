# Shared helper for review-orchestrate test files.
# Loaded in setup() so functions are available in tests.

# Run a Python expression against the orchestrate module, bound to `mod`.
_py() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
from cli import review_orchestrate as mod
$1
"
}

# Like _py but reads code from stdin (heredoc-safe for the nesting validator)
_py_here() {
  local code
  code=$(cat)
  _py "$code"
}

# Prior-run spend for a review rooted at $TMPDIR, printed to 2dp.
# $1 is PipelineState keyword arguments; only the state varies between cases.
_sum_costs() {
  _py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(head_sha='abc', $1)
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='/tmp/s.jsonl',
    )
    cost = mod._sum_existing_costs(job, state)
print(f'{cost:.2f}')
"
}
