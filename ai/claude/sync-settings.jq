# Syncs managed settings from a template into an existing settings file.
#
# Inputs (via --argjson):
#   $t — template  (ai/claude/settings.json)
#   $e — existing  (~/.claude/settings.json)
#
# Permissions (allow/deny arrays):
#   user_entries = existing entries NOT in $e._workbench (never touched)
#   result       = template entries + user_entries
#   _workbench   = updated to current template
#
# Hooks (keyed by event name, arrays of {matcher, hooks: [{type, command}]} objects):
#   Managed hooks are tracked by command string in $e._workbench.hooks.
#   User-added hooks are preserved; managed hooks are replaced with the template.
#
# Top-level keys (e.g. enabledPlugins) are added from the template only if absent.

# ── Permissions ──────────────────────────────────────────────────────────────
($e._workbench.permissions.allow // []) as $prev_allow |
($e._workbench.permissions.deny  // []) as $prev_deny  |
[($e.permissions.allow // [])[] | select(. as $x | $prev_allow | index($x) == null)] as $user_allow |
[($e.permissions.deny  // [])[] | select(. as $x | $prev_deny  | index($x) == null)] as $user_deny  |
($t.permissions.allow // []) as $new_allow |
($t.permissions.deny  // []) as $new_deny  |

# ── Hooks ────────────────────────────────────────────────────────────────────
# Hooks use matcher+hooks structure: [{matcher: "", hooks: [{type, command}]}]
# Build a merged hooks object: for each event in the template, remove previously
# managed hooks from existing, then prepend the new template hooks.
# Backward compat: extract commands from both old flat ({type,command}) and new
# nested ({matcher, hooks:[{type,command}]}) formats.
(($t.hooks // {}) | keys) as $hook_events |
(reduce $hook_events[] as $ev (
  ($e.hooks // {});
  ($e._workbench.hooks[$ev] // [] | [.[] | (.hooks[]?.command // .command) // empty]) as $prev_cmds |
  [(.[$ev] // [])[] | select(
    [(.hooks[]?.command // .command) // empty] | all(. as $c | $prev_cmds | index($c) == null)
  )] as $user_hooks |
  .[$ev] = ($t.hooks[$ev] + $user_hooks)
)) as $merged_hooks |
# _workbench tracking: store template hook entries per event
(reduce $hook_events[] as $ev (
  ($e._workbench.hooks // {});
  .[$ev] = $t.hooks[$ev]
)) as $wb_hooks |

# ── Assemble ─────────────────────────────────────────────────────────────────
$e
| .permissions.allow = ($new_allow + $user_allow)
| .permissions.deny  = ($new_deny  + $user_deny)
| .hooks = $merged_hooks
| ._workbench = {permissions: {allow: $new_allow, deny: $new_deny}, hooks: $wb_hooks}
| . + ($t | with_entries(select(.key != "permissions" and .key != "hooks" and (.key | in($e) | not))))
