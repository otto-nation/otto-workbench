---
title: Tools & Scripts
description: Complete catalog of workbench scripts, installed tools, and shell aliases, generated from the tool registries.
---

# Tools & Scripts Reference

Complete catalog of workbench scripts, installed tools, and shell aliases. Generated from the [tool registries](registries.md) and from the scripts themselves — nothing on this page is written here.

## Scripts

<!-- include: bin/local/generate-tool-context --emit scripts-table -->

## Script Reference

Each section below is the script's own header — the comment block under its shebang, or a Python script's docstring — so the description lives beside the code it describes and changes with it. Which scripts appear is the registries' decision: every `full` or `brief` tool is here unless its entry says `reference: false`, and a `hidden` one only when it says `reference: true` (see [Registries](registries.md#tool-entries)). The workbench's own validators and generators in `bin/local/` are documented in [CONTRIBUTING.md](https://github.com/otto-nation/otto-workbench/blob/main/CONTRIBUTING.md) instead.

How the AI subsystem behaves behind these entry points — review phases, publishing, settlement, the summary record — is in [AI Automation](ai-automation.md), and each module's own account is in [AI Libraries](ai-libraries.md).

**Workbench scripts** — general-purpose scripts installed onto `PATH` by `otto-workbench sync`.

<!-- include: bin/local/generate-doc-reference --set scripts --group workbench-scripts -->

**AI tooling** — the `pr` and `review` CLIs, the scanners the workbench skills drive, and the MCP launchers.

<!-- include: bin/local/generate-doc-reference --set scripts --group ai-tooling -->

## Installed Tools
<!-- include: bin/local/generate-tool-context --emit tools-table -->

## Adding a Tool

See [Registries](registries.md#adding-an-entry) for the full schema and step-by-step instructions. A script in a `bindir` registry needs a header before it can ship: the reference above is rendered from it, and the build fails when a script in the reference has none.
