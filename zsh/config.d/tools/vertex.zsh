# Vertex AI — give the Pi provider extension the project id under its own name
#
# The workbench spells the Vertex project id ANTHROPIC_VERTEX_PROJECT_ID, which
# is what ai/lib/vertex_quota.py and the Claude SDK read. Pi's google-vertex
# provider extension reads GOOGLE_CLOUD_PROJECT instead. Same value, two names.
#
# Only ever fills a gap: an operator who has set GOOGLE_CLOUD_PROJECT for some
# other Google tooling keeps it, because the two are the same variable to gcloud
# and overwriting it here would repoint every other consumer in the shell.
#
# duplicate-check: GOOGLE_CLOUD_PROJECT

[[ -n "${ANTHROPIC_VERTEX_PROJECT_ID:-}" ]] || return 0
[[ -z "${GOOGLE_CLOUD_PROJECT:-}" ]] || return 0

export GOOGLE_CLOUD_PROJECT="$ANTHROPIC_VERTEX_PROJECT_ID"
