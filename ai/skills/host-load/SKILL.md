---
name: host-load
description: "Diagnose why a herdr Hosts card or Linux user is loaded/strained — read-only, using host-health --json and the Hosts mod thresholds. TRIGGER when: a herdr host, machine, space, or user looks loaded, strained, or under heavy load (agent/agent-maximum, Hosts panel, swap/load on a workbench box). SKIP: production service outage (incident); code bug (debugger); killing or reaping processes."
source: otto-workbench/ai/skills/host-load/SKILL.md
invocation: "/host-load"
trigger: "Use when a herdr host, machine, space, or Linux user looks loaded, strained, or under heavy load, including Hosts-panel status and names like agent/agent-maximum."
skip: "Do not use for a production service outage (use incident) or a code bug (use debugger). Do not kill, reap, or restart anything."
---

# Host load

Read-only diagnosis of why a workbench machine (a Hosts card) or a Linux user
on one is loaded or strained. The Hosts panel is the owner of that picture;
this skill reads the same document and applies the same thresholds. It
modifies nothing.

Announce: "Using host-load to diagnose <name>."

## Do not

- Kill, reap, `killtree`, restart, `docker`, `otto-queue cancel`, or
  `pi-sessions close`
- Start with `ps aux`, `uptime`, or `top` — those are a second definition of
  load. `host-health --json` is the first probe, the same one
  `ai/herdr/app/mods/_lib/hosts_load.js` `loadHealth` runs
- Invent severity words or cutoffs. `ok` / `loaded` / `strained` and their
  reasons come from `severityOf` in `ai/herdr/app/mods/_lib/hosts.js`
- Run `herdr` (agents have no permission; it is not the Hosts card source)

## 1. Resolve the name

Hosts cards are machines in herdr-core's per-session `machines.json` (the
same remotes `_lib/machines.js` `remoteOns` polls). Read those files; do
not run `herdr machine list`.

```bash
STATE="${XDG_STATE_HOME:-$HOME/.local/state}"
cat "$STATE/herdr-core/default/machines.json" "$STATE/herdr-core/untrusted/machines.json"
```

Each file is a list of cards with `name`, SSH `target`, and `layout`. Skip
`layout === "unavailable"`. Main and untrusted windows keep disjoint lists;
read both so a card from either session is visible. The card `name` is what
the Hosts panel shows; `target` is the SSH host for section 2.

Match an exact card `name` first. A slash path (`agent/agent-maximum`) or a
hyphenated `<card>-<user>` token (`homelab-agent-maximum`) can still be a
card if it appears as `name` in those files — treat that as the host, no
user filter.

When the token is not an exact card:

- An exact machine name (or "this computer" / "this laptop") → that host,
  no user filter
- A `/` path: last segment is the Linux user; first segment is the machine
  when it matches a Hosts card
- A hyphenated token: if it starts with a Hosts-card `name` plus `-`, that
  card is the machine and the remainder is the Linux user
- If the machine segment does not match a card, pick the one strained or
  loaded host, and stop to ask when more than one is

## 2. Fetch `host-health --json`

Same argv as the panel. A missing `host-health` is **outdated**, not a cue
to fall back to `ps`.

```bash
host-health --json
```

On another herdr machine the panel uses herdr `exec` with `{on: NAME}`, which
loads workbench env then runs `host-health --json` (`REMOTE_PREAMBLE` in
`ai/herdr/app/mods/_lib/board_data.js`). Non-interactive SSH does not source
`~/.zshrc` or put `~/.local/bin` on `PATH`, so `ssh NAME host-health --json`
exits 127 on a healthy synced host. From a shell, match the preamble
(single-quoted so local `$HOME` is not expanded):

```bash
ssh NAME '. "$HOME/.config/zsh/env.sh" && workbench_env && host-health --json'
```

`NAME` is the Hosts card `name` when that is also an SSH host, otherwise
the card's `target`. A document without an object `load` and a `procs` key
is outdated (`hosts_load.js`); report that and stop.

## 3. Classify with Hosts thresholds

Read these constants from the files; do not copy new numbers into a prompt.

| Signal | loaded (`warn`) | strained (`fail`) | Source |
|---|---|---|---|
| load per core (`m1 / cores`) | ≥ `BUSY_PER_CORE` (1.5) | ≥ `STRAINED_PER_CORE` (3) | `_lib/load_backoff.js` |
| memory free (`available_mb / total_mb`) | < `LOADED_MEM_FREE` (0.10) | < `STRAINED_MEM_FREE` (0.05) | `_lib/hosts.js` |
| disk used (home or docker, worst) | ≥ `LOADED_DISK_USED` (0.90) | ≥ `STRAINED_DISK_USED` (0.95) | `_lib/hosts.js` |

Worst of the three is the Hosts word (`severityOf`). Unreachable or outdated
is `warn`, never `ok` (Hosts AGENTS.md).

`mem.swap_used_mb` is supporting evidence when the box feels slow; it is
**not** a Hosts severity input. Do not promote swap into `loaded`/`strained`.

## 4. Name the consumers

From that JSON only, in this order:

1. `procs.top_cpu` and `procs.top_mem` (what `topProcesses` draws)
2. `heavy` (`golangci-lint`, `go`, `gh`, `tasks`)
3. `queue` (`running` / `queued`, or `busy: true` while the ledger is locked)
4. `disk.home` / `disk.docker` when disk is a Hosts reason
5. `arc` (ZFS ARC) when memory is a Hosts reason — ARC counts inside used
   memory and is owned by no process in `procs.top_mem`

If a Linux user was named, keep rows whose `user` equals that name. Linux
`ps` may truncate (`agent-m+` for `agent-maximum`) — treat a truncated
`user` as a match when it is a prefix of the named user plus `+`.

When the named user is absent from both tops, one follow-up is allowed, on
the same host as section 2. Remote Linux (GNU `ps`):

```bash
ssh NAME '. "$HOME/.config/zsh/env.sh" && workbench_env && ps -u USER -o pid,user,pcpu,pmem,etime,stat,command --sort=-pcpu'
```

Local macOS (BSD `ps`; `--sort` is illegal there):

```bash
ps -r -u USER -o pid,user,pcpu,pmem,etime,stat,command
```

Replace `USER` with the resolved Linux user and `NAME` with the resolved
SSH host. Do not broaden to `ps aux`. Say whether the work is live (a
current pre-commit, `tsc`, queue job) or leftover (days-old probe, idle
agent). Stop at the consumers that explain the Hosts reasons.

## 5. Report

- **Hosts:** machine, word (`ok` / `loaded` / `strained`), reasons
- **Who:** processes and `heavy` / `queue` that explain those reasons; the
  Linux user when one was named
- **Live vs leftover**
- **Confidence:** High / Medium / Low
- **Recommended actions:** what the operator may do. Do not do them

Then stop.
