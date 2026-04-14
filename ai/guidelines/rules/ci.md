---
paths:
  - "**/.github/workflows/*.yml"
  - "**/.github/actions/**/*.yml"
---

# CI / GitHub Actions

## Security

- Never pipe remote scripts into sh (`curl | sh`, `wget | bash`) — if the upstream repo is compromised, this runs arbitrary code with access to CI secrets and push permissions. Use official GitHub Actions with pinned commit SHAs instead
- Always shell-quote values passed through `eval` — use `shlex.quote()` in Python, or equivalent. CI runs with elevated permissions (registry push, secrets access), so injection via config files is a real risk
- Pin GitHub Actions to full commit SHAs, not tags — tags can be force-pushed

## Tool Installation

- Use `pipx install` not bare `pip install` for CLI tools in CI — Ubuntu 24.04+ runners reject bare pip installs outside venvs with `externally-managed-environment`
- When using a GitHub Action that both installs and runs a tool (e.g., `golangci-lint-action`), set `install-only: true` if another step handles invocation — otherwise the action runs at the wrong scope or working directory

## Actions

- Before using any GitHub Action, read its README — check what it does by default, what inputs control behavior, and whether it runs something automatically. Many actions install AND execute; some have breaking changes between major versions
- When extracting a composite action, verify it works in all contexts it will be called from (matrix jobs vs single jobs, workspace root vs subdirectory)

## Scripts

- Understand command syntax before using — e.g., `unzip -d <dir> <filter>` treats extra args as file filters, not path components. Verify with `--help` or docs when uncertain

## Generated Files

- When CI depends on a generated file (lockfiles, version manifests), ensure a hook or CI check validates freshness — stale generated files cause silent behavioral drift
