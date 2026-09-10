# Vertex AI — give each consumer the value it reads under the name it reads it by
#
# One Vertex deployment, several tools, each with its own spelling for the same
# two facts. ~/.env.local carries the name Google's own tooling uses and this
# file fills in the aliases:
#
#   GOOGLE_CLOUD_PROJECT  → ANTHROPIC_VERTEX_PROJECT_ID  (ai/lib/agent/vertex_quota.py,
#                                                         the Claude Code CLI)
#   CLOUD_ML_REGION       → GOOGLE_CLOUD_LOCATION        (Pi's built-in google-vertex
#                                                         provider — Gemini)
#
# The second pair runs the other way round because the two names are not
# synonyms: CLOUD_ML_REGION is where the Anthropic models are provisioned and
# GOOGLE_CLOUD_LOCATION is where Google's are served. Equal on a machine using
# the global endpoint for both, and free to differ on one that is not — which is
# why every pair here only ever fills a gap. A value already set is a value some
# other consumer is relying on, and the two Google names in particular are read
# by every gcloud invocation in the shell.
#
# Without the location alias Pi's google-vertex provider declines to register,
# and the only symptom is Gemini missing from the model list — no error, on a
# machine whose credentials are fine.
#
# The mirror is attempted again at the first prompt when a pass leaves anything
# unresolved, because the source variables are not guaranteed to exist by the
# time this file runs. ~/.env.local is sourced ahead of every config layer and is
# where the workbench asks for these values, but ~/.zshrc's own machine-specific
# block sits *below* the line that sources the loader — a value exported there
# arrives after this file has already run and returned. The one-shot precmd hook
# below covers that case: it fires once, after ~/.zshrc has finished and before
# the first prompt.
#
# duplicate-check: (ANTHROPIC_VERTEX_PROJECT_ID|CLOUD_ML_REGION|GOOGLE_CLOUD_(PROJECT|LOCATION))
# duplicate-check-label: Vertex AI environment

# source:target, in that order. A plain array rather than an associative one:
# zsh does not promise iteration order for the latter, and a table read in a
# different order each shell is a table that hides a bug in one of its rows.
_wb_vertex_pairs=(
  'GOOGLE_CLOUD_PROJECT:ANTHROPIC_VERTEX_PROJECT_ID'
  'CLOUD_ML_REGION:GOOGLE_CLOUD_LOCATION'
)

# Returns 0 when nothing further is owed — every target is set, whether by this
# function or by someone else — and 1 while a source is still missing and a
# later attempt could still succeed.
_wb_vertex_mirror() {
  # `src` rather than `source`, which is the shell builtin: a local of that name
  # shadows nothing a command lookup uses, but reading it back is needlessly
  # ambiguous.
  local pair src target pending=0
  for pair in $_wb_vertex_pairs; do
    src="${pair%%:*}"
    target="${pair##*:}"

    # Already set: by the operator, by an earlier pass, or by another tool.
    # Whoever set it meant it, and overwriting would repoint their consumer.
    if [[ -n "${(P)target:-}" ]]; then
      continue
    fi
    if [[ -z "${(P)src:-}" ]]; then
      pending=1
      continue
    fi
    export "$target=${(P)src}"
  done
  return $pending
}

if _wb_vertex_mirror; then
  unset _wb_vertex_pairs
  unfunction _wb_vertex_mirror
  return 0
fi

autoload -Uz add-zsh-hook

# The retry. It unhooks and unloads itself whatever the outcome: a shell whose
# values never appear has nothing left to wait for, and should not pay for the
# check on every prompt for the rest of the session.
_wb_vertex_mirror_late() {
  _wb_vertex_mirror
  add-zsh-hook -d precmd _wb_vertex_mirror_late
  unset _wb_vertex_pairs
  unfunction _wb_vertex_mirror_late _wb_vertex_mirror
}

add-zsh-hook precmd _wb_vertex_mirror_late
