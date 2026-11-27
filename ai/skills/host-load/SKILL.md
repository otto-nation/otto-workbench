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

## 1. Resolve the name

Hosts cards are machines: this computer first, then `herdr machine list`
(the same remotes `_lib/machines.js` `remoteOns` polls). A slash path
(`agent/agent-maximum`) or a `machine-user` token (`homelab-agent-maximum`)
is not a card.

- An exact machine name (or "this computer" / "this laptop") → that host,
  no user filter
- Otherwise the last path segment is a Linux user. The first segment is the
  machine when it matches a Hosts card; if it does not, pick the one strained
  or loaded host, and stop to ask when more than one is

## 2. Fetch `host-health --json`

Same argv as the panel. A missing `host-health` is **outdated**, not a cue
to fall back to `ps`.

```bash
host-health --json
```

On another herdr machine the panel uses herdr `exec` with `{on: NAME}`, which
loads workbench env then runs `host-health --json`. From a shell:

```bash
ssh NAME host-health --json
```

`NAME` is the Hosts card / `herdr machine list` name when that is also an
SSH host. A document without an object `load` and a `procs` key is outdated
(`hosts_load.js`); report that and stop.

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

If a Linux user was named, keep rows whose `user` equals that name. Linux
`ps` may truncate (`agent-m+` for `agent-maximum`) — treat a truncated
`user` as a match when it is a prefix of the named user plus `+`.

When the named user is absent from both tops, one follow-up is allowed:

```bash
ps -u USER -o pid,user,pcpu,pmem,etime,stat,command --sort=-pcpu
```

Replace `USER` with the resolved Linux user. Do not broaden to `ps aux`.
Say whether the work is live (a current pre-commit, `tsc`, queue job) or
leftover (days-old probe, idle agent). Stop at the consumers that explain
the Hosts reasons.

## 5. Report

- **Hosts:** machine, word (`ok` / `loaded` / `strained`), reasons
- **Who:** processes and `heavy` / `queue` that explain those reasons; the
  Linux user when one was named
- **Live vs leftover**
- **Confidence:** High / Medium / Low
- **Recommended actions:** what the operator may do. Do not do them

Then stop.
