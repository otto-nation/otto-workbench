#!/usr/bin/env bash
# Foundation module: AI command loading, GitHub token resolution (handed off
# to ai/lib/pr/gh_token.py), response handling.
#
# Sourced first by `commit.sh`, `pr.sh`, and `review.sh`, and by the Taskfile
# tasks that drive them. It inherits the commit conventions by sourcing
# [`conventions.sh`](#conventionssh), so `COMMIT_TYPES` and the length limits
# have one owner across both halves.
#
# POSIX-compatible: go-task sources it through `sh -c`, which is why the array
# work lives in [`compact_diff.sh`](#aicompact_diffsh) instead.
#
# State set by its functions: `AI_COMMAND`, `AI_RESPONSE`.

# ─── Configuration ────────────────────────────────────────────────────────────
# shellcheck disable=SC2034  # All config variables are used by sourcing scripts (commit.sh, pr.sh, review.sh)

# Git convention constants (COMMIT_TYPES, COMMIT_HEADER_MAX_LEN, COMMIT_BODY_MAX_LEN)
# are defined in lib/conventions.sh — sourced here so AI automation inherits them.
# When sourced from bash (bin scripts), BASH_SOURCE resolves the path.
# When sourced from sh (Taskfile tasks), the root is WORKBENCH_LIB_DIR when the
# run pinned one and TASKFILE_DIR otherwise — both are set by go-task. Only this
# branch honours the pin: BASH_SOURCE names the file actually being read, and a
# stale WORKBENCH_LIB_DIR must never make a bash caller resolve its siblings out
# of a different checkout than the one it just loaded.
#
# Both branches resolve to a physical path, because the installed Taskfile is a
# symlink farm: `~/.config/task` holds a `lib` link into the checkout and nothing
# else, so a TASKFILE_DIR-relative root names a directory where only `lib/` is
# reachable. Sourcing `conventions.sh` through it works and everything under
# `ai/` does not, which is the shape this cost an afternoon to see.
if [ -n "${BASH_SOURCE:-}" ]; then
  _ai_core_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
else
  _ai_lib_root="${WORKBENCH_LIB_DIR:-${TASKFILE_DIR:?lib/ai/core.sh requires BASH_SOURCE, WORKBENCH_LIB_DIR, or TASKFILE_DIR}}"
  _ai_core_dir="$(cd "$_ai_lib_root/lib/ai" && pwd -P)" || {
    echo "lib/ai/core.sh: cannot resolve $_ai_lib_root/lib/ai (WORKBENCH_LIB_DIR=${WORKBENCH_LIB_DIR:-}, TASKFILE_DIR=${TASKFILE_DIR:-})" >&2
    unset _ai_lib_root
    return 1
  }
  unset _ai_lib_root
fi
# shellcheck source=/dev/null
. "$(dirname "$_ai_core_dir")/conventions.sh"
# GIT_REMOTE, resolve_default_branch, remote_branch_ref_exists and
# default_base_ref come from lib/git_remote.sh, which owns "which branch is
# trunk" for every caller. It lives outside lib/ai/ because the global pre-push
# hook needs the same answer and must not pay for this module to get it.
# shellcheck source=/dev/null
. "$(dirname "$_ai_core_dir")/git_remote.sh"
# The workbench checkout, kept because lib/ai/pr.sh runs with the *target* repo
# as its cwd and still has to find ai/lib/git/push.py. `_ai_core_dir` is <root>/lib/ai.
WORKBENCH_ROOT="$(dirname "$(dirname "$_ai_core_dir")")"
unset _ai_core_dir

# Maximum characters of diff content sent to the AI.
# Large diffs cause the AI CLI to reject the prompt entirely.
# When exceeded, complete per-file diffs are included greedily (smallest first);
# omitted files are listed by name so the AI still knows the full scope of changes.
DIFF_MAX_CHARS=8000

# When true, skips both issue-related prompts in generate_pr_content.
# Set by parse_pr_flags; pass --no-issue after -- in task invocations.
SKIP_ISSUE=false

# Global env file path — single source of truth is lib/constants.sh (TASKFILE_ENV).
# When sourced via Taskfile tasks (sh, not bash), lib/constants.sh is not available,
# so we fall back to the same value defined there.
: "${TASKFILE_ENV:="$HOME/.config/task/taskfile.env"}"

# Local per-project override takes priority over the global TASKFILE_ENV.
AI_LOCAL_ENV_PATH=".taskfile/taskfile.env"

# Markers the AI must use when returning PR content.
# Must stay in sync with the prompt in generate_pr_content.
PR_TITLE_MARKER="TITLE:"
PR_DESCRIPTION_MARKER="DESCRIPTION:"
# ──────────────────────────────────────────────────────────────────────────────

# _resolve_env_file — finds the active env file (local override or global).
# Prints the path to stdout. Returns 1 if neither exists.
_resolve_env_file() {
  if [ -f "$AI_LOCAL_ENV_PATH" ]; then
    echo "$AI_LOCAL_ENV_PATH"
  elif [ -f "$TASKFILE_ENV" ]; then
    echo "$TASKFILE_ENV"
  else
    return 1
  fi
}

# load_ai_command
# Finds the AI config and validates the binary exists.
# Sets AI_COMMAND. Returns 1 on failure.
load_ai_command() {
  local env_file
  env_file=$(_resolve_env_file) || {
    echo "✗ AI not configured. Run: task --global ai:setup"
    return 1
  }

  if ! grep -q "^AI_COMMAND=" "$env_file"; then
    printf "✗ AI_COMMAND not set in %s\n" "$env_file"
    return 1
  fi

  AI_COMMAND=$(grep "^AI_COMMAND=" "$env_file" | head -1 | cut -d'=' -f2-)
  local ai_bin
  ai_bin=$(echo "$AI_COMMAND" | cut -d' ' -f1)

  if ! command -v "$ai_bin" >/dev/null 2>&1; then
    printf "✗ AI command not found: %s\n" "$ai_bin"
    return 1
  fi

  # Optionally export ANTHROPIC_API_KEY for automation billing isolation.
  # When set in taskfile.env it overrides the interactive session key for this task run only.
  if grep -q "^ANTHROPIC_API_KEY=" "$env_file" 2>/dev/null; then
    # shellcheck disable=SC2034  # ANTHROPIC_API_KEY is read by the AI CLI subprocess
    ANTHROPIC_API_KEY=$(grep "^ANTHROPIC_API_KEY=" "$env_file" | head -1 | cut -d'=' -f2-)
    export ANTHROPIC_API_KEY
  fi
}

# load_gh_token
# Hands GitHub token resolution off to ai/lib/pr/gh_token.py and exports
# GH_TOKEN. Returns 1 on failure, with the guidance already on stderr.
load_gh_token() {
  # _gh_token is a plain global, not `local`: this file is sourced with `sh -c`
  # on the go-task path (see the header), and `local` inside a function run by
  # POSIX sh is non-standard. The `unset` on both the failure and success path
  # is what keeps a failed call from leaving `_gh_token` behind instead.
  _gh_token=$(python3 "$WORKBENCH_ROOT/ai/lib/pr/gh_token.py" --cwd .) || { unset _gh_token; return 1; }
  GH_TOKEN="$_gh_token"
  export GH_TOKEN
  unset _gh_token
}

# ceiling: AI_COMMAND is deliberately pluggable to non-Claude binaries, so this path
# cannot route through ai_backend; usage is recovered from the raw response afterwards
# instead. Route through the backend once every supported binary reports usage.
# Only a response carrying a usage envelope produces a ledger record — configure
# AI_COMMAND with `--output-format json` to get telemetry from Claude.

# True when the ai-usage-log helper binary is on PATH. Cached so run_ai's two
# call sites (_ai_unwrap, _ai_record) don't each repeat the PATH lookup.
_ai_usage_log_available() {
  command -v ai-usage-log > /dev/null 2>&1
}

# _ai_unwrap RAW_FILE
# Tees stdin to RAW_FILE and emits the reply text, unwrapping a JSON envelope when
# there is one. Passes stdin through untouched when the helper is unavailable.
_ai_unwrap() {
  local raw_file="$1"
  if _ai_usage_log_available; then
    ai-usage-log unwrap --tee "$raw_file"
    return 0
  fi
  cat
}

# _ai_record RAW_FILE TASK_LABEL EXIT_CODE
# Appends a ledger record when the raw response carried measurable usage.
_ai_record() {
  local raw_file="$1" task_label="$2" exit_code="${3:-0}"
  _ai_usage_log_available || return 0
  ai-usage-log record --from-log "$raw_file" --script task \
    --entry-point prompt --task "$task_label" --exit-code "$exit_code" || true
}

# run_ai PROMPT [AGENT_OVERRIDE] [TASK_LABEL]
# Requires AI_COMMAND.
# When AGENT_OVERRIDE is provided, replaces --agent <name> in AI_COMMAND
# so different tasks can route to the appropriate agent.
# TASK_LABEL names the call in the usage ledger.
# Sets AI_RESPONSE.
run_ai() {
  local prompt="$1"
  local agent_override="${2:-}"
  local task_label="${3:-ai-task}"

  local cmd="$AI_COMMAND"
  if [[ -n "$agent_override" ]]; then
    # Replace the agent name after --agent with the override value.
    # Uses sed because bash parameter expansion cannot match [^ ]* (non-space glob).
    # shellcheck disable=SC2001
    cmd=$(echo "$cmd" | sed "s/--agent [^ ]*/--agent $agent_override/")
  fi

  local raw_file
  raw_file=$(mktemp) || { echo "run_ai: mktemp failed" >&2; return 1; }
  local response_file
  response_file=$(mktemp) || { echo "run_ai: mktemp failed" >&2; rm -f "$raw_file"; return 1; }

  # shellcheck disable=SC2086  # $cmd holds "binary [flags]"; word-splitting is intentional
  # Redirect stderr to /dev/null — MCP server errors and CLI noise must not pollute
  # the captured response. load_ai_command already validated the binary exists.
  # Strip complete ANSI sequences (ESC + '[' + params + letter) before removing bare control chars.
  # Anchoring to \033 prevents the pattern from eating markdown checkboxes like [x] or [ ].
  # Written directly (not via command substitution) so PIPESTATUS below reflects
  # $cmd's real exit status instead of the subshell's.
  echo "$prompt" | $cmd 2>/dev/null | \
    _ai_unwrap "$raw_file" | \
    sed 's/\033\[[0-9;]*[a-zA-Z]//g' | \
    tr -d '\033\007\015' | \
    sed 's/^[> ]*//g' | \
    sed '/^```/d' > "$response_file"
  local cmd_exit="${PIPESTATUS[1]}"

  # shellcheck disable=SC2034  # AI_RESPONSE is read by callers after run_ai returns
  AI_RESPONSE=$(cat "$response_file")

  _ai_record "$raw_file" "$task_label" "$cmd_exit"
  rm -f "$raw_file" "$response_file"
}
