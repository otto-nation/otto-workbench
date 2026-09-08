---
title: AI Libraries
description: Every module in ai/lib/ — the Python behind pr review, pr comments, pr ci, and the eval harness.
---

# AI Libraries

Every module in `ai/lib/`, grouped by what it is for. This is the reference; [AI Automation](ai-automation.md) is the guide, and holds the setup, the configuration, and the flow that crosses several of these modules at once.

Each section below is the module's own docstring, rendered from `ai/lib/` by [`generate-doc-reference`](../bin/local/generate-doc-reference) — so the prose describing a module lives beside the code it describes, and a module that moves takes its documentation with it. A module declares which group it belongs to with a `# doc-group: <key>` comment under its docstring; nothing here lists module names, so adding a module changes only the module.

## Layers

The groups below say what a module is *for*. What it may *depend on* is a separate question, and each package answers it on the first line of its own `__init__.py`:

```python
"""Layer 4 — PR state and lifecycle. May import: core, config, git, gh, agent."""
```

That sentence is the only place a package's layer is written down. [`validate-ai-layers`](../bin/local/validate-ai-layers) parses it rather than carrying a table of its own, and fails any cross-package import the declaration does not permit — so the stack can be read off the imports, and `core` cannot be made to depend on `review` by an accident in a merge. Run it with no arguments to print the current stack.

The permitted set is narrower than the layer number alone: `gh` and `agent` both sit at layer 3 and neither may import the other. A package that declares no layer is itself a failure, which is what keeps a new one from joining the tree unplaced.

## Running a review

The orchestration of a review run: what it checks before spending anything, how the work is split into phases, what each agent is asked, and where the run's artifacts live.

<!-- include: bin/local/generate-doc-reference --set ai-lib --group pipeline -->

## Findings

What a review produces. Parsing an agent's output into findings, giving them stable IDs, merging duplicates, disproving the ones that do not hold up, and rendering what survives.

<!-- include: bin/local/generate-doc-reference --set ai-lib --group findings -->

## Publishing

Everything that leaves the machine — review comments, replies, summaries, tracking issues — and the draft gate they all pass through first.

<!-- include: bin/local/generate-doc-reference --set ai-lib --group publishing -->

## PR state

What a pull request is right now: its target, its threads, its CI, whether it has been pushed or rebased, and whether its reason to exist still holds.

<!-- include: bin/local/generate-doc-reference --set ai-lib --group pr-state -->

## AI backends

The provider plumbing every AI call goes through — backend selection, streamed events, usage accounting, and quota.

<!-- include: bin/local/generate-doc-reference --set ai-lib --group backend -->

## Evaluation

The eval harness: fixture tasks, the scorers that grade each task's output, and the aggregation the CI ratchet gates on.

<!-- include: bin/local/generate-doc-reference --set ai-lib --group eval -->

## Platform

The shared substrate — process execution, logging, the structured trail, serialization, config, paths, and the tool framework the CLIs are built on.

<!-- include: bin/local/generate-doc-reference --set ai-lib --group platform -->
