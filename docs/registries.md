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
  install_check: false               # filter tools by installed state (default: false)
  install_check_command: colima      # check if command exists in PATH
  install_check_symlink: ~/.docker   # check if symlink exists
  install_check_symlink_contains: x  # check if symlink target contains string
```

`loading` controls AI context generation: `always` puts tools in every Claude session; `scoped` (default) loads only when editing related files.

### Tool entries

```yaml
tools:
  - name: ripgrep                    # required
    description: "Fast regex search" # required
    when_to_use: "Searching files"   # required
    usage: "rg pattern | rg -t py"   # optional — pipe-separated examples
    docs: https://github.com/...     # optional
    brew_name: ripgrep               # optional — override for brewfile validation
    commands:                         # optional — subcommands
      - name: sync
        scope: "All components"
        when: "After pulling updates"
        detail: "Re-applies config"
```

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
| `docs/tools.md` tables | [`generate-tool-context`](../bin/local/generate-tool-context) | Humans |
| `docs/ai-automation.md` tables | [`generate-tool-context`](../bin/local/generate-tool-context) | Humans |
| `docs/components.md` lists | [`generate-tool-context`](../bin/local/generate-tool-context) | Humans |
| `.env.local.template` ENV section | [`generate-tool-context`](../bin/local/generate-tool-context) | Shell |

Freshness is enforced by the pre-push hook and CI — both run the generator and fail if the output differs.

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
2. Run `bin/local/generate-tool-context` — the variable will appear in `.env.local.template`

No other config edits are needed for any of these. The pre-push hook enforces that generated files are up to date.
