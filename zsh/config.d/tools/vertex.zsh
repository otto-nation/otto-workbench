# Vertex AI — give the Pi provider extension the project id under its own name
#
# The workbench spells the Vertex project id ANTHROPIC_VERTEX_PROJECT_ID, which
# is what ai/lib/agent/vertex_quota.py and the Claude SDK read. Pi's google-vertex
# provider extension reads GOOGLE_CLOUD_PROJECT instead. Same value, two names.
#
# Only ever fills a gap: an operator who has set GOOGLE_CLOUD_PROJECT for some
# other Google tooling keeps it, because the two are the same variable to gcloud
# and overwriting it here would repoint every other consumer in the shell.
#
# The mirror is attempted again at the first prompt when the first attempt found
# nothing, because the source variable is not guaranteed to exist by the time
# this file runs. ~/.env.local is sourced ahead of every config
# layer and is where the workbench asks for the project id, but ~/.zshrc's own
# machine-specific block sits *below* the line that sources the loader — an id
# exported there arrives after this file has already run and returned. The
# one-shot precmd hook below covers that case: it fires once, after ~/.zshrc has
# finished and before the first prompt.
#
# Without the retry the export is skipped in silence, and the only symptom is Pi
# starting with no models at all: its provider extension declines to register
# when it cannot find a project, rather than failing loudly.
#
# duplicate-check: GOOGLE_CLOUD_PROJECT

# Returns 0 when nothing further is owed — the mirror happened, or
# GOOGLE_CLOUD_PROJECT was already set by someone else — and 1 while the source
# variable is still missing and a later attempt could still succeed.
_wb_vertex_mirror_project() {
  [[ -z "${GOOGLE_CLOUD_PROJECT:-}" ]] || return 0
  [[ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]] || return 1
  export GOOGLE_CLOUD_PROJECT="$ANTHROPIC_VERTEX_PROJECT_ID"
}

if _wb_vertex_mirror_project; then
  unfunction _wb_vertex_mirror_project
  return 0
fi

autoload -Uz add-zsh-hook

# The retry. It unhooks and unloads itself whatever the outcome: a shell whose
# project id never appears has nothing left to wait for, and should not pay for
# the check on every prompt for the rest of the session.
_wb_vertex_mirror_project_late() {
  _wb_vertex_mirror_project
  add-zsh-hook -d precmd _wb_vertex_mirror_project_late
  unfunction _wb_vertex_mirror_project_late _wb_vertex_mirror_project
}

add-zsh-hook precmd _wb_vertex_mirror_project_late
