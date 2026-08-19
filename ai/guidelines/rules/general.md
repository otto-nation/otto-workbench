# General Coding Principles

## Workflow

- For decisions with meaningful trade-offs, present alternatives with a recommendation and wait for approval
- Always ask for confirmation before architectural decisions or significant changes
- When the user references a broken script, tool, or automation, fix the script's code — don't manually perform the action the script was supposed to automate
- Implement the practical fix before deep-diving into upstream or third-party source code. Ask before spending time on root-cause analysis in code you don't own

## Planning

Before code changes, walk this ladder — stop at the first unmet gate:

1. **Scope clear?** — target branch/worktree, what's in scope, and desired depth must all be unambiguous. If the user's prompt is ambiguous on any of these, ask before acting
2. **Plan needed?** — for multi-step modifications, present options with pros/cons and a recommendation. Wait for approval. Never proceed without it
3. **Plan verified?** — verify every API, method signature, and constant referenced against the actual codebase. Plans are written before reading code — specifics will be wrong
4. **Build** — each phase independently committable, shippable state. If too large for one commit, split it

Plans describe *what* and *why* — not implementation details.
Save plans to `ignore/plans/`, specs to `ignore/specs/`.

- A plan's out-of-scope section names the issue tracking each entry. Filing deferred work is required by `issue-tracker.md` § Writing an issue (any tracker); the plan is where that gets skipped, because listing something under "out of scope" already feels like disposing of it. An entry with no issue behind it is not deferred, it is dropped

## Code Quality

Reuse ladder — stop at the first rung that solves the problem:

1. **Already in this codebase?** Reuse it — before writing new code, grep for existing implementations of the same logic. Check sibling files, shared libs, and format/helper directories
2. **Stdlib / language built-in?** Use it
3. **Already-installed dependency?** Use it
4. **One line?** Write it inline — don't extract a function or file for what fits in one line
5. **New utility function?** Write the minimum
6. **New dependency?** Justify it

- Never introduce changes that violate SSOT or DRY — if data or logic already has a single owner, reference it instead of duplicating. Before adding a constant, config value, or pattern, check if it already exists elsewhere
- When renaming a service, endpoint, or wire format, audit all references — not just call sites. Check: doc comments, inline examples, container network aliases, Helm/Pkl defaults, env var values, test fixtures, and generated config
- When changing a wire format (message subjects, event schemas, API contracts), document deployment ordering in the PR description — which services deploy first, whether simultaneous deploy is required, and what breaks during the rollout window
- Never swallow errors silently — propagate them or return an explicit error. Key/map lookups on external data (DB, API, user input) must use safe-access patterns (comma-ok in Go, `.get()` in Python, `in` checks in JS) and handle the missing-key case
- Fix review findings in the current PR. Defer one to a tracking issue or a follow-up PR only when I have explicitly agreed to that deferral — ask, don't assume
- When automation fails partway through, make it idempotent and re-runnable rather than adding checkpoint/retry/resume logic
- When a linter (nesting depth, ShellCheck, errexit, bare-refs) flags a file the current change set touched, fix every violation in that file — not only the one that failed the check. Pre-existing violations in a file we already modified are in scope; violations in files we did not touch are not
- Never delete or move a spec, plan, doc, or test file to get past a failing pre-push or pre-commit check. Fix the underlying violation, or stop and ask — bypassing the check by removing its input silently discards work

## Debugging

On failure, diagnose in this order — do NOT retry with variations:

1. **Root cause** — investigate why, not just what. Resource limit hit? Find the consumer, don't raise the limit
2. **Diagnostics** — was the diagnostic path sufficient? If you manually reconstructed data that should have been persisted, add instrumentation as part of the fix
3. **Persist** — structured files (JSON) over transient console output; surviving successful runs, not just failures

## Code Style

### Types Over Tuples
- Never return a new tuple carrying more than one piece of business data — model it as a frozen dataclass, struct, or the language's equivalent. A tuple cannot gain a field without breaking every destructuring call site, and it forces callers to learn field order instead of reading names
- The language's error convention is not a tuple in this sense. Go's `(T, error)`, comma-ok map reads and type assertions, and their equivalents in other languages are the idiom — keep them. This rule governs a return carrying two pieces of *data*, not a value paired with a failure channel
- Never widen an existing tuple return to carry another field. Converting it to a type is the fix; `(a, b)` → `(a, b, c)` is not
- Give the type a predicate that reads honestly at the call site (`result.ok`) rather than making callers infer status from which fields came back empty
- Pre-existing tuples you are not otherwise touching stay as they are — converting them is its own change

### Enums Over String Literals
- A fixed set of named states is an enum, not string literals. When those states are persisted or cross a wire, keep the string values stable so existing state files still load — the enum is for the code, not a format break

### Comments & Documentation
- Comments should be production-ready; place them above the line, never inline
- Do not add comments that exist only to explain what a prompt change did
- Never cite an issue or PR number in a code comment as provenance for a past bug — state the behavior the code guarantees instead. A number is only warranted when the comment is a live signal (a `ceiling:` trigger, a TODO pointing at pending work)
- Silent fallbacks and defense-in-depth patterns require a comment explaining intent
- Mark deliberate simplifications with a `// ceiling:` comment naming the tradeoff and upgrade trigger — e.g. `// ceiling: global lock, upgrade to per-account locks if throughput matters`
- A trigger is a *condition*, not an intention. Write the clause that says when the shortcut stops being acceptable — one turning on `if`, `once`, `when`, `unless`, or `until`, or an explicit `Upgrade trigger:` sentence. "Upgrade when we get around to it" names nothing and reads as no trigger at all
- When a simplification genuinely has no upgrade path, write `// ceiling-permanent:` and say why the alternative is worse. It is counted apart from the pending ones — inventing a fake threshold to satisfy the gate is the outcome this form exists to prevent
- The marker opens its own comment line and may run to the end of the comment block below it, so the trigger does not have to fit on the first line. `bin/local/validate-ceiling` fails on any `ceiling:` marker with neither a trigger nor the permanent form
- When adding docs, extend existing files rather than creating new ones
- When adding CLI commands or changing command signatures, update `docs/ai-automation.md` and/or `README.md`

## Testing

- Write tests the same way as existing tests in the project
- Tests are not complete until they run and all pass
- Never disable a test as a fix for a failing test
- Do not add tests that simply assert constant values
- When a foundational method's contract changes, audit every test that asserts the old behavior and update it
- Prefer real dependencies over mocks when feasible — mocks hide integration bugs
- Every bug fix and behavioral change must include a regression test
