---
paths:
  - "**/bin/local/**"
  - "**/lib/*.sh"
  - "**/install.components"
---
# Tools — workbench
<!-- AUTO-GENERATED — do not edit directly -->
<!-- Regenerate: generate-tool-context -->

## Workbench Dev Scripts
- **validate-registries** — Validates all tool registry YAML files for schema correctness and cross-file consistency
- **validate-components** — Validates all component framework contracts — Tier 1 sync_<name>() presence, Tier 2 registry consistency
- **validate-migrations** — Validates migration file naming, function naming, and shebang conventions
- **validate-errexit** — Validates bash scripts for dangerous && patterns that silently exit under set -e
- **validate-skills** — Validates SKILL.md frontmatter conventions — required fields, name/directory consistency, lifecycle field pairing
- **validate-cli-flags** — Validates CLI flag conventions — no --repo alias, --pr/--branch mutual exclusivity
- **generate-tool-context** — Generates tools.generated*.md rule files from the domain registries
