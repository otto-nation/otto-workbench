# Syncs managed settings from a template into Pi's live global settings file.
#
# Inputs (via --argjson):
#   $t       — template (ai/pi/settings.json)
#   $e       — existing (~/.pi/agent/settings.json)
#   $allowed — template packages this machine was confirmed to reach
#   $blocked — template packages this machine was confirmed not to reach
#   $models  — the Pi half of `config models` (lib/config_cli.py): the model
#              keys config.yml's models.* name, each absent when none does
#
# Scalar keys from the template and model config override the live file on every
# sync, so the workbench stays authoritative over extensions and `pi config`.
# This is deliberate: every template scalar (defaultThinkingLevel,
# enableInstallTelemetry) is workbench-managed and must not drift because an
# extension or a one-off `pi config` edit set it first.
# Model keys come from config.yml — the same place Claude Code's sync reads — and
# are applied after template scalars so they always win. defaultProvider is not
# a template scalar: it is the session's provider when models.session names one,
# and otherwise whatever Pi or the operator set.
#
# A model reference written without a provider reaches here as a bare id. Its
# enabledModels entry and modelThinkingLevels key are qualified with the
# defaultProvider the merged file ends up with, because Pi adds a bare pattern
# once per provider serving it, with a warning every start, and keys
# modelThinkingLevels by exact provider/id. With no defaultProvider, entries
# stay bare and bare level keys are dropped, since nothing could match them.
#
# modelThinkingLevels is merged into the live object rather than replacing it:
# Ctrl+S in /thinking saves a level there for whatever model is current, and
# that choice is the operator's. A level the config stops naming therefore stays
# until it is cleared by hand.
#
# `packages` is reconciled rather than overridden, because it is a list: adding
# an entry displaces nothing, and override semantics would strip packages an
# extension installed.
#
# $allowed and $blocked do not have to cover every template package. A package
# whose reachability could not be determined — no gh, no network, no auth —
# appears in neither and its current state is left untouched, so an offline sync
# neither installs a package it cannot verify nor strips one that already works.
#
# Withdrawal is therefore a reachability verdict, not a diff against the template.
# A package the template stops declaring is left in the live file, because
# nothing here can tell it from one the operator installed — ai/claude's
# equivalent answers that with a manifest recording what it managed, kept in a
# sidecar under the state root precisely because Claude Code rejects the whole
# settings file when it finds workbench bookkeeping inside it. Removing a
# package the workbench once installed is a migration, the same way this branch
# removes the settings file the sync once wrote to the wrong path.
#
# Entries are identified the way Pi identifies them: by source with any trailing
# @ref removed, so a pinned ref and an object-form entry carrying filters both
# match the plain source string and are left alone rather than duplicated.

def source_of: if type == "object" then .source else . end;
def ident: source_of | sub("@[^@/:]+$"; "");
def qualified($provider): if test("/") or $provider == null then . else "\($provider)/\(.)" end;
def first_seen: reduce .[] as $m ([]; if index($m) then . else . + [$m] end);

($t | del(.packages)) as $scalars |
($e.packages // []) as $existing |
($blocked | map(ident)) as $blocked_idents |

[$existing[] | select((ident) as $i | ($blocked_idents | index($i)) == null)] as $kept |
($kept | map(ident)) as $kept_idents |
($kept + [$allowed[] | select((ident) as $i | ($kept_idents | index($i)) == null)]) as $packages |

$e
| . + $scalars
| . + $models
| .defaultProvider as $provider
| if $models | has("enabledModels")
  then .enabledModels |= (map(qualified($provider)) | first_seen)
  else . end
| if $models | has("modelThinkingLevels")
  then .modelThinkingLevels = (($e.modelThinkingLevels // {})
    + ($models.modelThinkingLevels | with_entries(.key |= qualified($provider) | select(.key | test("/")))))
    | if .modelThinkingLevels == {} then del(.modelThinkingLevels) else . end
  else . end
| if $packages == [] and (($e | has("packages")) | not) then .
  else .packages = $packages
  end
