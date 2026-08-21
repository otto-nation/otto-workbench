---
title: AI Libraries
description: Every module in ai/lib/ — the Python behind pr review, pr comments, pr ci, and the eval harness.
---

# AI Libraries

Every module in `ai/lib/`, grouped by what it is for. This is the reference; [AI Automation](ai-automation.md) is the guide, and holds the setup, the configuration, and the flow that crosses several of these modules at once.

Each section below is the module's own docstring, rendered from `ai/lib/` by [`generate-doc-reference`](../bin/local/generate-doc-reference) — so the prose describing a module lives beside the code it describes, and a module that moves takes its documentation with it. A module declares which group it belongs to with a `# doc-group: <key>` comment under its docstring; nothing here lists module names, so adding a module changes only the module.

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
