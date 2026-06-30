---
name: simplify-audit
description: "Whole-repo audit for over-engineering and deletion opportunities. TRIGGER when: user asks to audit a codebase for complexity, find dead code, identify unnecessary abstractions, or simplify a project. SKIP: reviewing a specific diff or PR (use /simplify or /code-review); ceiling debt markers (use /ceiling-debt)."
source: otto-workbench/ai/claude/skills/simplify-audit/SKILL.md
invocation: "/simplify-audit [path]"
trigger: "audit for over-engineering, find dead code, simplify the codebase, unnecessary abstractions, what can we delete, repo cleanup"
skip: "Diff-scoped review (use /simplify), PR review (use /code-review), ceiling markers (use /ceiling-debt)"
output: "Tagged findings to stdout, ranked by impact"
---

# Simplify Audit

Scan the entire repo (or a specified directory) for over-engineering, dead code, and deletion opportunities. Produces tagged findings ranked by impact.

---

## Arguments

- `path` (optional): Directory to audit. Defaults to the repo root.

---

## Tags

Each finding uses exactly one tag:

| Tag | Meaning | Example |
|-----|---------|---------|
| `delete:` | Dead code, unused flexibility, speculative feature | Unused helper function, feature-flagged code with no flag |
| `stdlib:` | Hand-rolled code the standard library ships | Custom `deepCopy()` when `structuredClone` exists |
| `native:` | Dependency doing what the platform already does | npm package for something CSS or the browser API handles |
| `yagni:` | Abstraction with only one implementation | Interface with a single concrete type |
| `shrink:` | Same logic, fewer lines | Verbose conditional that collapses to a one-liner |

---

## Steps

### Step 1: Determine scope

Get the audit target — use the skill argument if provided, otherwise:
```bash
git rev-parse --show-toplevel
```

### Step 2: Survey the codebase

Read the project's `.claude/anatomy.md` if it exists to understand file structure. Otherwise, list the top-level directories and key files to understand what languages and frameworks are in use.

### Step 3: Audit

Walk through the codebase systematically. For each source directory, examine files for:

1. **Dead code** (`delete:`) — exported functions/types with no callers, unused imports, commented-out code, feature flags with no toggle
2. **Stdlib replacements** (`stdlib:`) — hand-rolled utilities that duplicate standard library functionality
3. **Unnecessary dependencies** (`native:`) — packages that replicate platform capabilities (browser APIs, CSS features, language built-ins)
4. **Single-implementation abstractions** (`yagni:`) — interfaces/abstract classes with exactly one concrete implementation, factory functions that produce one type, config systems with one consumer
5. **Verbose patterns** (`shrink:`) — multi-line code that can be expressed more concisely without losing clarity

### Step 4: Output findings

Print each finding on one line:

```
<tag> <what to cut>. <replacement>. [path:line]
```

Examples:
```
delete: unused `formatCurrency` export. Remove. [src/utils/format.ts:42]
stdlib: hand-rolled `deepMerge`. Use structuredClone or Object.assign. [lib/merge.js:1]
native: `date-fns` only used for `format()`. Use Intl.DateTimeFormat. [package.json]
yagni: `StorageProvider` interface with only `LocalStorage` impl. Inline it. [src/storage/index.ts:5]
shrink: 12-line null-check cascade. Optional chaining. [src/api/client.ts:88]
```

Rank by impact — biggest deletion opportunity first.

### Step 5: Summary line

End with a net-impact summary:

```
net: -N lines, -M deps possible.
```

If the codebase is already lean:

```
Lean already. Ship.
```
