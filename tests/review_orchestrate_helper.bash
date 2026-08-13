# Shared helper for review-orchestrate test files.
# Loaded in setup() so functions are available in tests.

# Run Python expression importing from the orchestrate script
_py() {
  python3 -c "
import sys, importlib.util, importlib.machinery
loader = importlib.machinery.SourceFileLoader('orch', '$ORCHESTRATE')
spec = importlib.util.spec_from_loader('orch', loader)
mod = importlib.util.module_from_spec(spec)
sys.modules['orch'] = mod
spec.loader.exec_module(mod)
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
