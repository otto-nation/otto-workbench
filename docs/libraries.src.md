---
title: Libraries
description: All shared code in lib/ — the modules loaded through the ui.sh facade and those sourced directly.
---

# Libraries

All shared code lives in `lib/`. Most modules are loaded through the `ui.sh` facade; some are sourced directly by specific consumers.

Each section below is the module's own header comment, rendered from `lib/` by [`generate-doc-reference`](../bin/local/generate-doc-reference) — so the prose describing a module lives beside the code it describes, and its function table is read out of the file rather than restated here.

A function's Purpose cell is the first paragraph of its doc comment, in full. Rationale that belongs to the implementation rather than the contract goes below a blank comment line, where the reader who opens the file finds it and the table does not carry it.

## Loading

Scripts source `lib/ui.sh` via `git rev-parse --show-toplevel` — depth-independent:

```bash
_SELF="$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
. "$(git -C "$(dirname "$_SELF")" rev-parse --show-toplevel)/lib/ui.sh"
```

`ui.sh` is a facade: the modules it sources are the ones marked *Loaded via `ui.sh`* below. Every other module is sourced directly by its consumers. `roots.sh` reaches the facade through `constants.sh`, and is also sourced on its own by consumers that need the roots without the rest of the framework.

## Core Modules

<!-- include: bin/local/generate-doc-reference --set lib --group core -->

## Registry & Config Modules

<!-- include: bin/local/generate-doc-reference --set lib --group registry -->

## AI Modules (`lib/ai/`)

These modules power the AI-driven git automation (commits, PRs, reviews). All are sourced directly by Taskfile tasks — none go through the `ui.sh` facade.

<!-- include: bin/local/generate-doc-reference --set lib --group ai -->
