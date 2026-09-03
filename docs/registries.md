---
title: Registries
description: Each tooling directory owns a registry.yml — the single source of truth for tool documentation, AI context, and validation.
---

# Registries

Each tooling directory owns a `registry.yml` describing the tools it provides. Registries are the single source of truth for tool documentation — they feed AI context generation, doc tables, and validation.

## File Types

| Pattern | Purpose | Owns |
|---------|---------|------|
| `*/registry.yml` | Component tool registries | Tool docs (`tools[]`) |
| `**/*.registry.yml` | Brew stack registries (by domain) | Tool docs (`tools[]`) |
| `**/*.env.yml` | Environment variable declarations | Env vars (`env[]`, `auth`) |

**Key separation:** `registry.yml` owns tool documentation. `*.env.yml` owns env var and auth declarations, colocated with the code that reads them. Env vars set programmatically at runtime (e.g., `DOCKER_HOST`) are not declared in registries.

## Discovery

[`collect_registries()`](../lib/registries.sh) auto-discovers all registry files via glob patterns — adding a new registry requires only creating the file in the right location. No edits to any index or config.

## Schema

### Meta block

```yaml
meta:
  section: "Brew Tools"              # H2 heading in generated output (required for tools)
  validation: brewfile               # cross-validation mode (see below)
  source: brew/Brewfile              # relative path for cross-validation
  loading: scoped                    # "always" or "scoped" (default: scoped)
  install_check: true                # gate entire registry on install state (optional)
  install_check_command: colima      # check if command exists in PATH
  install_check_symlink: ~/.docker   # check if symlink exists (~ and ${WORKBENCH_*_DIR} expand)
  install_check_symlink_contains: x  # check if symlink target contains string
  claude_env: true                   # mirror this registry's env[] into Claude's settings.json
```

`loading` controls AI context generation: `always` puts tools in every Claude session; `scoped` (default) loads only when editing related files.

`install_check` gates the entire registry at runtime — when true, the registry is skipped by summary and env-var rendering if the check command/symlink is absent. Used for optional components (Docker, AWS). Omit for registries that are always active.

`install_check_symlink` accepts a leading `~` and the three workbench roots, written as `${WORKBENCH_CONFIG_DIR}`, `${WORKBENCH_STATE_DIR}`, or `${WORKBENCH_CACHE_DIR}` — for example `${WORKBENCH_STATE_DIR}/docker-aliases.zsh`. A leading `~` and those three tokens are substituted literally rather than evaluated, so no other shell syntax is expanded. Reference a root this way instead of writing its default path: a hardcoded `~/.config/workbench/...` breaks the moment the root moves. See [Libraries — roots.sh](libraries.md#rootssh).

### Tool entries

```yaml
tools:
  - name: ripgrep                    # required
    description: "Fast regex search" # required
    permission: true                 # required — see below
    visibility: full                 # required — see below
    when_to_use: "Searching files"   # required when visibility: full
    usage: "rg pattern | rg -t py"   # required when visibility: full
    docs: https://github.com/...     # optional
    brew_name: ripgrep               # optional — override for brewfile validation
    commands:                         # optional — subcommands
      - name: sync
        scope: "All components"
        when: "After pulling updates"
        detail: "Re-applies config"
```

`permission` is the Bash permission the tool earns in Claude Code's `settings.json`:

| Value | Grant |
|-------|-------|
| `false` | none — internal or indirectly invoked tools |
| `true` | `Bash(<name>:*)` |
| `"cmd"` | `Bash(cmd:*)`, when the CLI name differs from the registry name |
| `["Bash(cmd sub:*)"]` | the patterns verbatim, for granular subcommand control |

`visibility` is both the AI-context gate and the rendering style:

| Value | Rendering |
|-------|-----------|
| `full` | full entry in `tools.generated.md` — heading, description, when-to-use, usage |
| `brief` | one-liner — name and description |
| `hidden` | omitted from AI context |

`when_to_use` and `usage` are required when `visibility: full` and forbidden otherwise.

### Environment variables (`*.env.yml`)

```yaml
env:
  - var: DOCKER_DEFAULT_PLATFORM     # required — must match ^[A-Z][A-Z0-9_]*$
    comment: "Default Docker platform"
    default: "linux/amd64"
    setup_url: https://...
    prefix: "linux/"

auth:
  env_var: CONTEXT7_API_KEY          # required within auth block
  setup_url: https://...
  prefix: "ctx7_"
```

Env var names must be unique across all registries — the validator enforces no duplicates.

### `claude_env` — variables Claude Code needs without a shell

A Claude Code session started outside an interactive shell — the desktop app, a launchd job, an IDE extension — inherits nothing from `~/.env.local`, so a variable that decides how the CLI routes has to reach it through `~/.claude/settings.json` instead. `meta.claude_env: true` marks a registry whose `env[]` belongs in that file: on every sync, [`step_claude_settings`](../ai/claude/steps.sh) reads the values `~/.env.local` sets for those variables and writes them into the settings file's `env` block.

The block is a mirror, not a merge. A variable dropped from `~/.env.local` is removed from the settings file too — settings.json wins over the environment in Claude Code, so an entry left behind could not be overridden from a shell afterwards. Variables under `env` that no flagged registry declares were put there by hand and are left alone, and a machine with no `~/.env.local` at all is left alone entirely.

The flag is opt-in per registry because the two files have different audiences: `~/.env.local` holds API tokens and is the operator's alone, while `~/.claude/settings.json` is written world-readable. Set it only on a registry whose variables are all safe to publish there — routing, model selection, region — never on one declaring a credential. `~/.zshrc` is not a place for these either: the config layers are sourced before it, so a value exported there is invisible to them (see [Execution Flow](execution-flow.md)) and the mirror never sees it at all.

## Cross-Validation Modes

Set `meta.validation` to enable cross-checking between the registry and its source:

| Mode | Checks | Source |
|------|--------|--------|
| `brewfile` | Every tool name (or `brew_name`) exists in the Brewfile | `meta.source: brew/Brewfile` |
| `bindir` | Every tool name exists as a file in the directory | `meta.source: bin` |
| `zsh-comments` | Every tool name has a matching `# keyword` comment | `meta.source: zsh/config.d/...` |
| `none` | Schema-only validation | N/A |

Run `bin/local/validate-registries` to check all registries. The pre-push hook runs this automatically.

## Generated Output

Registries feed into several generated files:

| Output | Generator | Loaded by |
|--------|-----------|-----------|
| `tools.generated.md` | [`generate-tool-context`](../bin/local/generate-tool-context) | Claude (path-scoped) |
| `tools.workflow.generated.md` | [`generate-tool-context`](../bin/local/generate-tool-context) | Claude (every session) |
| every `docs/<name>.md` with a `docs/<name>.src.md` beside it | [`compose-docs`](../bin/local/compose-docs) | Humans |

The composed docs ask for their generated sections with an include directive:

```markdown
<!-- include: bin/local/generate-tool-context --emit scripts-table -->
```

`compose-docs` runs the named command and substitutes its output, so the doc names the block it wants and the generator answers — neither keeps a list of the other's sections. `bin/local/generate-tool-context --help` lists the blocks it can emit.

Freshness is enforced by the pre-push hook and CI — `validate-all` runs [`validate-docs-composed`](../bin/local/validate-docs-composed) for the composed docs, and both re-run the generator for the `tools.generated*.md` rule files.

## Adding an Entry

### Adding a brew tool

1. Add the formula or cask to `brew/Brewfile`
2. Add an entry to the appropriate `brew/*.registry.yml`
3. Run `bin/local/generate-tool-context` to regenerate

### Adding a bin script

1. Create the script in `bin/`
2. Add an entry to `bin/registry.yml`
3. Run `bin/local/generate-tool-context`

### Adding environment variables

1. Create or edit a `*.env.yml` next to the code that reads the variable
2. Run `bin/local/generate-tool-context`

No other config edits are needed for any of these. Run `bin/local/compose-docs` as well if the entry appears in a composed doc. The pre-push hook enforces that generated files are up to date.
