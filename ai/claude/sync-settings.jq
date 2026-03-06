# Syncs managed permission entries from a template into an existing settings file.
#
# Inputs (via --argjson):
#   $t — template  (ai/claude/settings.json)
#   $e — existing  (~/.claude/settings.json)
#
# Per array (allow/deny):
#   user_entries = existing entries NOT in $e._workbench (never touched)
#   result       = template entries + user_entries
#   _workbench   = updated to current template
#
# Top-level keys (e.g. enabledPlugins) are added from the template only if absent.

($e._workbench.permissions.allow // []) as $prev_allow |
($e._workbench.permissions.deny  // []) as $prev_deny  |
[($e.permissions.allow // [])[] | select(. as $x | $prev_allow | index($x) == null)] as $user_allow |
[($e.permissions.deny  // [])[] | select(. as $x | $prev_deny  | index($x) == null)] as $user_deny  |
($t.permissions.allow // []) as $new_allow |
($t.permissions.deny  // []) as $new_deny  |
$e
| .permissions.allow = ($new_allow + $user_allow)
| .permissions.deny  = ($new_deny  + $user_deny)
| ._workbench = {permissions: {allow: $new_allow, deny: $new_deny}}
| . + ($t | with_entries(select(.key != "permissions" and (.key | in($e) | not))))
