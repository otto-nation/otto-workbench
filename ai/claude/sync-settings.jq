# Syncs managed settings from a template into an existing settings file.
#
# Inputs (via --argjson):
#   $t — template  (ai/claude/settings.json)
#   $e — existing  (~/.claude/settings.json)
#   $m — manifest  (what the last sync wrote; {} on a machine that has none)
#
# Output is an envelope, not a settings file:
#   {settings: …, manifest: …}
# The caller writes .settings to ~/.claude/settings.json and .manifest to
# $CLAUDE_SETTINGS_MANIFEST. The manifest used to ride along inside the settings
# file under a `_workbench` key, but Claude Code rejects a settings file that
# declares hook entries under any key other than `hooks` — and skips the file
# entirely, so every permission and hook in it goes with it. `$e._workbench` is
# still read as a fallback so the first sync after the split inherits what the
# last in-file stamp recorded, and the key is deleted from the settings output.
#
# Permissions (allow/deny arrays):
#   user_entries = existing entries NOT in the manifest (never touched)
#   result       = template entries + user_entries
#   manifest     = updated to current template
#
# Hooks (keyed by event name, arrays of {matcher, hooks: [{type, command}]} objects):
#   Managed hooks are tracked by command string in the manifest's hooks.
#   User-added hooks are preserved; managed hooks are replaced with the template.
#
# Top-level keys (e.g. enabledPlugins) are added from the template only if absent.
# `env` is deliberately not one of them: ai/claude/steps.sh mirrors that block
# from ~/.env.local after this script runs, because a key added here once is a
# key no later sync ever corrects.

(if ($m | length) > 0 then $m else ($e._workbench // {}) end) as $prev |

# ── Permissions ──────────────────────────────────────────────────────────────
($prev.permissions.allow // []) as $prev_allow |
($prev.permissions.deny  // []) as $prev_deny  |
($prev.permissions.additionalDirectories // []) as $prev_dirs |
[($e.permissions.allow // [])[] | select(. as $x | $prev_allow | index($x) == null)] as $user_allow |
[($e.permissions.deny  // [])[] | select(. as $x | $prev_deny  | index($x) == null)] as $user_deny  |
[($e.permissions.additionalDirectories // [])[] | select(. as $x | $prev_dirs | index($x) == null)] as $user_dirs |
($t.permissions.allow // []) as $new_allow |
($t.permissions.deny  // []) as $new_deny  |
($t.permissions.additionalDirectories // []) as $new_dirs |

# ── Hooks ────────────────────────────────────────────────────────────────────
# Hooks use matcher+hooks structure: [{matcher: "", hooks: [{type, command}]}]
# Build a merged hooks object: for each event in the template, remove previously
# managed hooks from existing, then prepend the new template hooks.
# Backward compat: extract commands from both old flat ({type,command}) and new
# nested ({matcher, hooks:[{type,command}]}) formats.
(($t.hooks // {}) | keys) as $hook_events |
(reduce $hook_events[] as $ev (
  ($e.hooks // {});
  ($prev.hooks[$ev] // [] | [.[] | (.hooks[]?.command // .command) // empty]) as $prev_cmds |
  [(.[$ev] // [])[] | select(
    [(.hooks[]?.command // .command) // empty] | all(. as $c | $prev_cmds | index($c) == null)
  )] as $user_hooks |
  .[$ev] = ($t.hooks[$ev] + $user_hooks)
)) as $merged_hooks |
# Manifest tracking: store template hook entries per event
(reduce $hook_events[] as $ev (
  ($prev.hooks // {});
  .[$ev] = $t.hooks[$ev]
)) as $wb_hooks |

# ── Assemble ─────────────────────────────────────────────────────────────────
{
  settings: (
    $e
    | del(._workbench)
    | .permissions.allow = ($new_allow + $user_allow)
    | .permissions.deny  = ($new_deny  + $user_deny)
    | .permissions.additionalDirectories = ($new_dirs + $user_dirs | unique)
    | .hooks = $merged_hooks
    | . + ($t | with_entries(select(.key != "permissions" and .key != "hooks" and (.key | in($e) | not))))
  ),
  manifest: {
    permissions: {allow: $new_allow, deny: $new_deny, additionalDirectories: $new_dirs},
    hooks: $wb_hooks
  }
}
