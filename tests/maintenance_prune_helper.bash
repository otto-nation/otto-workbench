#!/usr/bin/env bash
# PATH/stub builders shared by maintenance_prune.bats and maintenance_pressure.bats.
# Callers set TEST_HOME, SCRIPT, STUB_BIN, NARROW, CALLS, then invoke these.

# _build_narrow — symlink-only PATH, no /usr/bin or /bin appended.
_build_narrow() {
  local tool real
  ln -sf "$BASH" "$NARROW/bash"
  for tool in python3 yq git awk du dirname date cat mkdir mv rm sed tr mktemp head sort; do
    real="$(_runnable_binary "$tool" || true)"
    if [[ -z "$real" ]]; then
      echo "narrow: no runnable $tool on PATH; leaving it out" >&2
      continue
    fi
    ln -sf "$real" "$NARROW/$tool"
  done
}

_stub_df() {
  cat > "$STUB_BIN/df" <<EOF
#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted on\n'
printf '/dev/disk 100 50 50 %s%% /\n' "\${STUB_DF_PCT:-50}"
EOF
  chmod +x "$STUB_BIN/df"
}

_stub_go() {
  cat > "$STUB_BIN/go" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == env && "\$2" == GOCACHE ]]; then
  printf '%s\n' "$TEST_HOME/gocache"
  exit 0
fi
if [[ "\$1" == clean && "\$2" == -cache ]]; then
  printf '%s\n' "\$*" >> "$CALLS"
  rm -rf "$TEST_HOME/gocache"
  mkdir -p "$TEST_HOME/gocache"
  exit 0
fi
exit 1
EOF
  chmod +x "$STUB_BIN/go"
}

_stub_mise() {
  cat > "$STUB_BIN/mise" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$CALLS"
rm -rf "$TEST_HOME/mise/installs/old"
exit 0
EOF
  chmod +x "$STUB_BIN/mise"
}

_stub_docker() {
  cat > "$STUB_BIN/docker" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$CALLS"
if [[ "\$1" == context && "\$2" == inspect ]]; then
  printf 'unix://%s\n' "$TEST_HOME/docker.sock"
  exit 0
fi
if [[ "\$1" == image ]]; then
  printf 'Total reclaimed space: %s\n' "\${STUB_DOCKER_RECLAIMED:-1.5GB}"
  exit 0
fi
exit 1
EOF
  chmod +x "$STUB_BIN/docker"
}

_write_config() {
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf '%s\n' "$1" > "$WORKBENCH_CONFIG_DIR/config.yml"
}

# _run_prune — from `/`, the cwd launchd gives the timer. env -i so the
# ambient PATH cannot smuggle in the machine's real go/mise.
_run_prune() {
  run --separate-stderr env -i \
    HOME="$TEST_HOME" \
    PATH="$STUB_BIN:$NARROW" \
    TMPDIR="$TMPDIR" \
    WORKBENCH_STATE_DIR="$WORKBENCH_STATE_DIR" \
    WORKBENCH_CACHE_DIR="$WORKBENCH_CACHE_DIR" \
    WORKBENCH_CONFIG_DIR="$WORKBENCH_CONFIG_DIR" \
    MISE_DATA_DIR="$TEST_HOME/mise" \
    CALLS="$CALLS" \
    STUB_DF_PCT="${STUB_DF_PCT:-50}" \
    DOCKER_HOST="${DOCKER_HOST:-}" \
    bash -c "cd / && '$SCRIPT'"
}
