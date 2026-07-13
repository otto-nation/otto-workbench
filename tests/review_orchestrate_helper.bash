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
