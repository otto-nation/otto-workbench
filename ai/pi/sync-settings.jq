# Syncs managed settings from a template into Pi's live global settings file.
#
# Inputs (via --argjson):
#   $t — template (ai/pi/settings.json)
#   $e — existing (~/.pi/agent/settings.json)
#   $allowed — template packages this machine was confirmed to reach
#   $blocked — template packages this machine was confirmed not to reach
#
# Scalar keys are seeds, not overrides: a template key is written only when the
# live file does not already carry it. Whatever set the value first keeps it —
# an extension, `pi config`, or Ctrl+S in /model. The cost of not overriding is
# that changing a template default never reaches a machine that already has the
# key; delete the key locally to be re-seeded.
#
# `packages` is reconciled rather than seeded, because it is a list: adding an
# entry displaces nothing, and seed-only semantics would withhold the package
# from every machine that has ever run `pi install`.
#
# $allowed and $blocked do not have to cover every template package. A package
# whose reachability could not be determined — no gh, no network, no auth —
# appears in neither and its current state is left untouched, so an offline sync
# neither installs a package it cannot verify nor strips one that already works.
#
# Entries are identified the way Pi identifies them: by source with any trailing
# @ref removed, so a pinned ref and an object-form entry carrying filters both
# match the plain source string and are left alone rather than duplicated.

def source_of: if type == "object" then .source else . end;
def ident: source_of | sub("@[^@/:]+$"; "");

($t | del(.packages)) as $scalars |
($e.packages // []) as $existing |
($blocked | map(ident)) as $blocked_idents |

[$existing[] | select((ident) as $i | ($blocked_idents | index($i)) == null)] as $kept |
($kept | map(ident)) as $kept_idents |
($kept + [$allowed[] | select((ident) as $i | ($kept_idents | index($i)) == null)]) as $packages |

$e
| . + ($scalars | with_entries(select(.key | in($e) | not)))
| if $packages == [] and (($e | has("packages")) | not) then .
  else .packages = $packages
  end
