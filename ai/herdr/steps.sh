#!/usr/bin/env bash
# description: Herdr agent multiplexer — persistent panes and agent state for Pi and Claude Code
# Herdr setup steps — sourced by ai/setup.sh and ai/steps.sh.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).
#
# Herdr is an operator tool: it owns agent terminals and shows which agent is
# waiting on you. Agents are deliberately given no permission to drive it, so
# it never becomes a second job facility beside job_start/run_in_background.
# Sync also themes the herdr TUI and desktop app (Gruvbox, following system
# light/dark).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

# shellcheck source=untrusted_window.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/untrusted_window.sh"
# shellcheck source=mods_install.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/mods_install.sh"
# shellcheck source=layouts_install.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/layouts_install.sh"

# step_install_herdr — installs herdr via its vendor installer when not on PATH.
# Keeping an installed herdr current is step_update_herdr's job.
step_install_herdr() {
  install_via_installer herdr "$HERDR_INSTALL_URL" "Herdr"
  # The installer writes LOCAL_BIN_DIR/herdr. That directory is often missing
  # from PATH until the next login, so later steps in this same run would skip.
  if ! command -v herdr > /dev/null 2>&1; then
    export PATH="$LOCAL_BIN_DIR:$PATH"
  fi
}

# step_update_herdr — moves herdr to the current release.
#
# Plain `herdr update` leaves compatible running servers alive; --handoff is
# experimental and is never passed. Stdin is closed so a restart prompt cannot
# block an unattended sync; the call's runtime is not bounded. Failure (and a
# zero-exit "was not updated") surfaces the last non-empty line of herdr's own
# output — the reason comes after any banner or progress lines, and a Homebrew
# install prints the brew upgrade command there — rather than a hardcoded
# `herdr update`. Non-fatal: an offline machine still gets the rest of its
# config, as with step_update_pi.
#
# Inside a herdr pane the update is not attempted at all: herdr refuses to
# update from within its own session ("run `herdr update` outside herdr after
# detaching"), so the attempt can only fail, and every interactive sync from a
# herdr pane reported it as a warning the operator could not act on there.
# The unattended maintenance sync runs outside any session and performs the
# update, so this is a skip, not a failure. Detected the way herdr's own
# integrations detect a pane: HERDR_ENV=1 with a pane id.
step_update_herdr() {
  command -v herdr > /dev/null 2>&1 || { warn "herdr not found in PATH — skipping"; return 0; }
  if [[ "${HERDR_ENV:-}" == 1 && -n "${HERDR_PANE_ID:-}" ]]; then
    skip "herdr update — inside a herdr session; the next sync outside one (maintenance, or after detaching) updates it"
    return 0
  fi
  local output status=0 last_line
  output=$(herdr update < /dev/null 2>&1) || status=$?
  last_line=$(printf '%s\n' "$output" | awk 'NF { line = $0 } END { print line }')
  if [[ "$status" -ne 0 || "$output" == *"was not updated"* ]]; then
    warn "herdr was not updated${last_line:+: $last_line}"
  else
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "herdr is current" || true
  fi
  return 0
}

# step_herdr_integrations — installs herdr's Pi and Claude Code integrations
# for whichever harness is configured on this machine.
#
# Herdr owns the integration content and versions it with the binary, so this
# runs after step_update_herdr and re-runs every sync — herdr asks for a
# reinstall after upgrades. The Pi file is a flat .ts the Pi extension prune
# never touches; the Claude hook entry is not in the workbench settings
# manifest, so sync-settings.jq preserves it.
step_herdr_integrations() {
  command -v herdr > /dev/null 2>&1 || { warn "herdr not found in PATH — skipping"; return 0; }
  local harness dir
  for harness in pi claude; do
    case "$harness" in
      pi)     dir="$PI_AGENT_DIR" ;;
      claude) dir="$CLAUDE_DIR" ;;
    esac
    [[ -d "$dir" ]] || continue
    if herdr integration install "$harness" > /dev/null 2>&1; then
      [[ "${WORKBENCH_SYNC:-}" != true ]] && success "herdr $harness integration installed" || true
    else
      warn "Could not install herdr's $harness integration — run: herdr integration install $harness"
    fi
  done
  return 0
}

# step_herdr_machine_note — tells the operator how to register a remote
# machine when herdr's catalog has none.
#
# Informational only: registering is left to the operator because an
# interactive `herdr machine add` may install herdr on the remote, and the
# workbench never learns a host name. Silent inside a regular sync (it would
# repeat for anyone using herdr locally on purpose) and on any error.
step_herdr_machine_note() {
  [[ "${WORKBENCH_SYNC:-}" == true ]] && return 0
  command -v herdr > /dev/null 2>&1 || return 0
  local listing count
  listing=$(herdr machine list --json 2> /dev/null) || return 0
  count=$(jq 'if type == "array" then length else -1 end' <<< "$listing" 2> /dev/null) || return 0
  if [[ "$count" == "0" ]]; then
    info "herdr: no remote machines registered — add one with: herdr machine add <ssh-host> --label <name>"
  fi
  return 0
}

# _herdr_app_present — true when the app bundle or its config dir exists, or a
# herdr-app other than the workbench's own launcher (ai/herdr/bin/herdr-app,
# installed with herdr whether or not the app is) is on PATH.
_herdr_app_present() {
  [[ -x "$HERDR_APP_BUNDLE/Contents/MacOS/Herdr" || -d "$HERDR_APP_DIR" ]] && return 0
  local found
  found=$(command -v herdr-app 2>/dev/null) || return 1
  ! workbench_symlink_owned "$found" "ai/herdr/bin"
}

# _herdr_app_link_themes [HOME] — symlinks every generated
# ai/herdr/app/themes/<name>.generated.json into HOME/themes (default
# HERDR_APP_DIR) as <name>.json, the name herdr-app reads the theme by.
# Identical regular file → replaced by the link; differing regular file →
# warned about and left (it is the person's edit).
# shellcheck disable=SC2120 # HOME is optional; tests pass it, sync does not
_herdr_app_link_themes() {
  local home="${1:-$HERDR_APP_DIR}"
  local themes_dir="$home/themes"
  mkdir -p "$themes_dir"
  local src name target
  for src in "$HERDR_APP_THEMES_SRC_DIR"/*"$HERDR_APP_THEME_SUFFIX"; do
    [[ -f "$src" ]] || continue
    name=$(basename "$src" "$HERDR_APP_THEME_SUFFIX").json; target="$themes_dir/$name"
    if [[ -f "$target" && ! -L "$target" ]] && ! cmp -s "$src" "$target"; then
      warn "herdr-app theme $name: $target differs from the workbench copy — left as is; delete it and re-run otto-workbench sync to link it"
      continue
    fi
    if [[ -f "$target" && ! -L "$target" ]]; then
      rm "$target"
    fi
    install_symlink "$src" "$target" "herdr-app theme $name" --no-prompt
  done
  return 0
}

# _herdr_app_set_theme [HOME] — sets settings.json's `theme` to the light/dark
# pair and nothing else. Never clobbers: a file that is not one JSON object, or
# a symlink, is warned about and left. HOME defaults to HERDR_APP_DIR.
# shellcheck disable=SC2120 # HOME is optional; tests pass it, sync does not
_herdr_app_set_theme() {
  local home="${1:-$HERDR_APP_DIR}"
  local light="$HERDR_APP_THEME_LIGHT" dark="$HERDR_APP_THEME_DARK" desired current updated
  local file="$home/settings.json"
  desired=$(jq -cn --arg l "$light" --arg d "$dark" '{light: $l, dark: $d}')
  if [[ -L "$file" ]]; then
    warn "$file is a symlink — left as is; set theme there to $desired"
    return 0
  fi
  current='{}'
  if [[ -e "$file" ]]; then
    current=$(cat "$file") || { warn "Could not read $file"; return 0; }
    jq -se 'length == 1 and (.[0] | type == "object")' <<< "$current" >/dev/null 2>&1 \
      || { warn "$file is not a JSON object — left as is; fix it and re-run otto-workbench sync"; return 0; }
  fi
  if jq -e --argjson t "$desired" '.theme == $t' <<< "$current" >/dev/null 2>&1; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "herdr-app theme = $light / $dark" || true
    return 0
  fi
  updated=$(jq --argjson t "$desired" '.theme = $t' <<< "$current") \
    && wb_write_json_file "$file" "$updated" \
    || { warn "Could not write $file — herdr-app theme not set"; return 0; }
  success "herdr-app theme = $light / $dark"
  return 0
}

# step_herdr_app_theme — gives herdr-app the workbench's Gruvbox light/dark themes,
# following the system appearance. No-op when herdr-app is not installed. Non-fatal.
step_herdr_app_theme() {
  _herdr_app_present || return 0
  _herdr_app_link_themes
  _herdr_app_set_theme
  return 0
}

# step_herdr_app_mods — copies workbench herdr-app mods into HERDR_APP_DIR/mods
# and, once the launcher has created it, HERDR_UNTRUSTED_APP_DIR/mods, so both
# windows carry the same mods. No-op when herdr-app is not installed.
# Prints a keys.json binding suggestion the first time the prs mod is installed;
# never edits keys.json. Non-fatal: always returns 0.
step_herdr_app_mods() {
  _herdr_app_present || return 0
  _herdr_app_install_mods
  local name
  for name in "${HERDR_APP_MODS_NEWLY_INSTALLED[@]}"; do
    [[ "$name" == prs ]] || continue
    info 'herdr-app: bind a key to open the PR board by adding to keys.json: "<key>": {"command": "tab.new", "args": {"name": "PRs", "mod": "prs"}}'
    break
  done
  [[ -d "$HERDR_UNTRUSTED_APP_DIR" ]] && _herdr_app_install_mods "$HERDR_UNTRUSTED_APP_DIR"
  return 0
}

# step_herdr_app_layouts — derives workbench herdr-app layouts from main.json.
# Also derives them in HERDR_UNTRUSTED_APP_DIR once the launcher has created it,
# from that window's own main.json, tracked in its own manifest.
# No-op when herdr-app is not installed. Prints a layout.switch hint the first time workbench is installed.
# Non-fatal: always returns 0.
step_herdr_app_layouts() {
  _herdr_app_present || return 0
  _herdr_app_install_layouts
  local name
  for name in "${HERDR_APP_LAYOUTS_NEWLY_INSTALLED[@]}"; do
    [[ "$name" == workbench ]] || continue
    info 'herdr-app: show the workbench layout (a PRs toggle beside Files/Changes opens the board) with: herdr-app do layout.switch --name workbench'
    break
  done
  [[ -d "$HERDR_UNTRUSTED_APP_DIR" ]] \
    && _herdr_app_install_layouts "$HERDR_UNTRUSTED_APP_DIR" "$HERDR_UNTRUSTED_APP_LAYOUTS_MANIFEST"
  return 0
}

# _herdr_config_swap FILE CANDIDATE — moves CANDIDATE over FILE, then asks
# `herdr config check`; on failure puts the original back (or removes FILE when
# there was none) and warns with herdr's diagnostic. Returns 1 when rolled back.
_herdr_config_swap() {
  local file="$1" candidate="$2" backup="" check diag
  if [[ -e "$file" ]]; then
    backup=$(mktemp "$file.XXXXXX") && cp -p "$file" "$backup" \
      || { rm -f "$candidate" "$backup"; warn "Could not back up $file — herdr theme not set"; return 1; }
  fi
  mv "$candidate" "$file" || { rm -f "$candidate" "$backup"; warn "Could not write $file — herdr theme not set"; return 1; }
  if ! check=$(herdr config check 2>&1); then
    if [[ -n "$backup" ]]; then
      mv "$backup" "$file" || { warn "herdr rejected $file and restoring it failed — original is at $backup"; return 1; }
    else
      rm -f "$file"
    fi
    diag=$(printf '%s\n' "$check" | awk '/config: issues found/{p=1; next} p && NF {print; exit}')
    warn "herdr rejected $file after the theme change — restored it${diag:+: $diag}"
    return 1
  fi
  rm -f "$backup"
  return 0
}

# _herdr_reload_config — asks a running herdr server to re-read config.toml.
# No server is not an error: the next start reads the file.
_herdr_reload_config() {
  local out
  out=$(herdr server reload-config 2>&1) && return 0
  [[ "$out" == *server_not_running* ]] && return 0
  warn "herdr did not reload its config — run: herdr server reload-config"
  return 0
}

# step_herdr_tui_theme — sets the herdr TUI's [theme] to the Gruvbox light/dark
# pair with auto_switch on, so it follows the terminal's appearance. Owns only
# name, auto_switch, light_name and dark_name; every other line of config.toml is
# left as it was (lib/toml_table.py verifies that). A symlinked, unparseable, or
# un-editable file is warned about and left. Non-fatal.
#
# A narrow step under #1660: once config.toml is rendered from a managed
# template, the template carries these keys and this step goes.
step_herdr_tui_theme() {
  command -v herdr > /dev/null 2>&1 || return 0
  local file="$HERDR_CONFIG_FILE" candidate msg status=0
  local label="herdr theme = $HERDR_TUI_THEME_LIGHT / $HERDR_TUI_THEME_DARK (auto-switch)"
  if [[ -L "$file" ]]; then
    warn "$file is a symlink — left as is; set [theme] there: name=$HERDR_TUI_THEME_DARK auto_switch=true light_name=$HERDR_TUI_THEME_LIGHT dark_name=$HERDR_TUI_THEME_DARK"
    return 0
  fi
  mkdir -p "$HERDR_CONFIG_DIR" && candidate=$(mktemp "$file.XXXXXX") \
    || { warn "Could not prepare $file — herdr theme not set"; return 0; }
  msg=$(python3 "$LIB_SRC_DIR/toml_table.py" set "$file" theme --out "$candidate" \
          name="\"$HERDR_TUI_THEME_DARK\"" auto_switch=true \
          light_name="\"$HERDR_TUI_THEME_LIGHT\"" dark_name="\"$HERDR_TUI_THEME_DARK\"" 2>&1) || status=$?
  case "$status" in
    0) ;;
    3) rm -f "$candidate"; [[ "${WORKBENCH_SYNC:-}" != true ]] && success "$label" || true; return 0 ;;
    *) rm -f "$candidate"; warn "herdr theme not set — ${msg:-toml_table.py exited $status}; $file left as is"; return 0 ;;
  esac
  _herdr_config_swap "$file" "$candidate" || return 0
  success "$label"
  _herdr_reload_config
  return 0
}

# sync_herdr — runs the herdr sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
#
# A machine without herdr is left alone rather than installed onto: the
# install belongs to setup, where the operator chose the tool. herdr-app is
# not handled here — see sync_herdr_app.
sync_herdr() {
  command -v herdr > /dev/null 2>&1 || return 0

  sync_header "herdr host"
  step_update_herdr

  sync_header "herdr integrations"
  step_herdr_integrations

  sync_header "herdr theme"
  step_herdr_tui_theme

  step_herdr_machine_note
}

# sync_herdr_app — themes herdr-app wherever it is installed.
#
# Called by sync_ai ahead of its tool-selection check, not through the
# sync_<tool> convention: herdr-app is a separate program from the herdr TUI,
# and gating it on the `herdr` entry in ai.tools would leave a machine that
# runs the app without that selection unthemed. The app's own presence is
# the gate.
sync_herdr_app() {
  _herdr_app_present || return 0
  sync_header "herdr-app theme"
  step_herdr_app_theme
  sync_header "herdr-app mods"
  step_herdr_app_mods
  sync_header "herdr-app layouts"
  step_herdr_app_layouts
  sync_header "herdr-app untrusted window"
  step_herdr_untrusted_window
  sync_component_bin "$HERDR_SRC_DIR"
}

register_herdr_steps() {
  register_step "Install herdr"      step_install_herdr
  register_step "Update herdr"       step_update_herdr
  register_step "Herdr integrations" step_herdr_integrations
  register_step "Herdr theme"        step_herdr_tui_theme
  register_step "Herdr machines"     step_herdr_machine_note
  register_step "Herdr app theme"         step_herdr_app_theme
  register_step "Herdr app mods"          step_herdr_app_mods
  register_step "Herdr app layouts"       step_herdr_app_layouts
  register_step "Herdr untrusted window"  step_herdr_untrusted_window
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Herdr sync${NC}\n"
  sync_herdr
  sync_herdr_app
  echo
  success "Herdr sync complete!"
fi
