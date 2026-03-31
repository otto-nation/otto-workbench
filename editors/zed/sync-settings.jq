# Merges managed keys from the workbench template into the existing Zed settings.
#
# Inputs (via --argjson):
#   $t — template  (editors/zed/settings.json)
#   $e — existing  (~/.config/zed/settings.json, JSONC comments pre-stripped)
#
# Keys listed in $t._workbench are workbench-managed and always overwritten.
# All other keys in $e are preserved unchanged.
# _workbench is never written to the live file.

($t._workbench // []) as $managed |
reduce $managed[] as $key ($e; .[$key] = $t[$key])
| del(._workbench)
