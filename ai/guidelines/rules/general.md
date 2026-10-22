# General Coding Principles

## Workflow

- For decisions with meaningful trade-offs, present alternatives with a recommendation and wait for approval
- Always ask for confirmation before architectural decisions or significant changes
- When the user references a broken script, tool, or automation, fix the script's code — don't manually perform the action the script was supposed to automate
- Implement the practical fix before deep-diving into upstream or third-party source code. Ask before spending time on root-cause analysis in code you don't own

## Upstream Repositories

A repository, framework, or library we do not own is never where a bug is assumed to live. When a dependency misbehaves, the cause is almost always our usage of it — a wrong flag, a misread contract, a stale version, a config the docs say to set — and an upstream issue or PR built on that misreading wastes a maintainer's time and puts our mistake on public record.

- Never open an issue, a PR, or a discussion on a repo outside our own organizations, and never comment on one, without the user's explicit approval for that specific filing. Drafting the text is fine; posting it is the user's call
- Before even proposing an upstream filing, do the due diligence and present it: read the dependency's documentation and changelog for the version we actually run, check that we are on a current release, search the upstream tracker (open *and* closed) for the same report, and read the upstream source along the failing path
- Prove it is not our misuse with a minimal reproduction outside our code — a fresh project, the documented invocation, nothing of ours in it. If the bug only reproduces through our code, it is our bug until shown otherwise
- State, for every way we could be misusing it, what rules that out. "Looks like an upstream bug" with no reproduction and no ruled-out alternatives is a hypothesis about our code, not a finding about theirs
- When the due diligence finds our misuse — the expected outcome — fix our code and say what the misreading was. Do not file upstream to ask for a clearer error or a doc change unless the user asks for it
- A workaround for a genuine upstream bug goes in our code with a `ceiling:` comment naming the upstream issue as its trigger, so it is removed when the fix lands
- Enforced by `claude-bash-guard` under Claude Code and the `upstream-guard` extension under Pi (cheap pre-filter, then `owners-check`), by `gh.client` for the workbench's own publishing, and by `owners-check` itself. Unconfigured `github.owners` refuses every GitHub write. `WORKBENCH_ALLOW_UPSTREAM=1` is honoured by `gh.client` only, never by the guards; an agent command that names it is refused

## Choosing What to Recommend

The recommendation is the option you would still defend a year from now, not the one with the smallest diff. Size, effort, and "fewest files touched" are costs to state, never reasons to choose.

- Before recommending a fix, ask what let the defect happen and whether the option removes that cause or only this instance. Two matchers that must agree, two error paths for one condition, a value computed in two places: a fix that makes them agree today leaves them free to drift tomorrow — the structural fix makes the disagreement unrepresentable (one matcher, one sentinel, one owner)
- Check every option against the codebase's own written conventions — ADRs, the repo's AGENTS.md or CLAUDE.md, the pattern its siblings follow — and say which one each option honours or breaks. An option that matches the nearest code but contradicts a documented decision is the outlier, not the precedent
- Verify the claims an option rests on before presenting it. "Everything else does X" is a grep, not a memory; one counter-example inside the same subsystem changes the recommendation
- When the easiest option genuinely is the best long-term one, say why it is — that it is easy is not the argument
- If the long-term fix is larger than the task, present it anyway as the recommendation, with its size and what it would touch, and offer the smaller fix as the alternative. Scoping it down is the user's call, not a filter applied before they see it
- The reuse ladder below and `reuse.level` govern how a chosen design is *built* — reuse before writing, shortest diff that implements it. They never choose *which* design to recommend

## Planning

Before code changes, walk this ladder — stop at the first unmet gate:

1. **Scope clear?** — target branch/worktree, what's in scope, and desired depth must all be unambiguous. If the user's prompt is ambiguous on any of these, ask before acting
2. **Plan needed?** — for multi-step modifications, present options with pros/cons and a recommendation. Wait for approval. Never proceed without it
3. **Plan verified?** — verify every API, method signature, and constant referenced against the actual codebase. Plans are written before reading code — specifics will be wrong
4. **Build** — each phase independently committable, shippable state. If too large for one commit, split it

Plans describe *what* and *why* — not implementation details.

Plans and specs go in the repository's **workspace**, which is not inside any
checkout. Run `resolve-workspace` for the path — it prints `<container>/workspace`,
the directory holding the bare `.git` that every worktree is a peer of — and write
plans to `plans/` and specs to `specs/` under it. Never derive the path yourself;
one script owns it and the guards below refuse what disagrees with it.

A plan is about the repository, not about one branch of it, which is the whole
reason it does not live in a worktree: written there it is duplicated across every
sibling checkout, invisible from the others, and deleted by the `wt remove` that
retires the branch — taking the reasoning with it while the work it explains lives
on in `main`. Nothing at the container is inside a checkout, so there is no
`.gitignore` entry to keep and no chance of committing one by accident.

`resolve-workspace` exits 1 on an ordinary clone, which has no container. That is
a refusal, not a prompt to improvise: convert the repo with `wt-init`. Writing to
`<repo>/workspace` instead would put the artifact back inside the checkout, which
is the arrangement the rule exists to end.

This is enforced rather than asked for. `claude-edit-guard` under Claude Code and
the `workspace-guard` extension under Pi both refuse a plan or spec write landing
inside a worktree — including the `docs/superpowers/specs/` the brainstorming
skill defaults to, which this rule overrides by the skill's own terms.
A plan's out-of-scope section names the issue tracking each entry — see
`issue-tracker.md` § Writing an issue (any tracker). An entry with no issue behind
it is not deferred, it is dropped.

## Code Quality

Reuse ladder — stop at the first rung that solves the problem. It decides how to build the design already chosen, not which design to choose (see § Choosing What to Recommend):

1. **Already in this codebase?** Reuse it — before writing new code, grep for existing implementations of the same logic. Check sibling files, shared libs, and format/helper directories
2. **Stdlib / language built-in?** Use it
3. **Already-installed dependency?** Use it
4. **One line?** Write it inline — don't extract a function or file for what fits in one line
5. **New utility function?** Write the minimum
6. **New dependency?** Justify it

- Match on boundaries, not substrings — a path suffix check needs a separator (`/` or `Path.parts`); a pattern against structured text (YAML keys, log prefixes, headers) needs an anchor (`^`, `\A`, or a parse). Bare `endswith`/`contains` treats `myhandler.go` as `handler.go`. A separator on one end is not a boundary either: `startsWith(name + "-")` still matches `name-pro-123`, because the separator is inside the neighbour's name. Anchor both ends, or match the parsed structure
- Never introduce changes that violate SSOT or DRY — if data or logic already has a single owner, reference it instead of duplicating. Before adding a constant, config value, or pattern, check if it already exists elsewhere
- When renaming a service, endpoint, or wire format, audit all references — not just call sites. Check: doc comments, inline examples, container network aliases, Helm/Pkl defaults, env var values, test fixtures, and generated config
- When changing a wire format (message subjects, event schemas, API contracts), document deployment ordering in the PR description — which services deploy first, whether simultaneous deploy is required, and what breaks during the rollout window
- Never swallow errors silently — propagate them or return an explicit error. Key/map lookups on external data (DB, API, user input) must use safe-access patterns (comma-ok in Go, `.get()` in Python, `in` checks in JS) and handle the missing-key case
- Validate an input where it enters, not where it is used. A guard reached only on one branch means validation is decided by the data rather than by the contract — the same call passes unchecked whenever an earlier value sends it down the other path. An early return for a legitimate case (a grace period, an empty set) silently skips every cross-field check that sat below it. Check the whole input contract at the top, before the first branch or early return
- Functions that format, serialize, or report on a value must not mutate the argument they were handed — return a new structure instead. A reporting helper that inserts a key is a side effect nobody reads the signature for
- Never index into or take the first element of an unsorted filesystem listing (`glob`, `Path.glob`, `os.listdir`, `Path.iterdir`) — directory order is filesystem-dependent, so the "first" file is a different file on another machine. Sort first
- Never use a process-randomized hash (Python's built-in `hash()`) for an ID, cache key, or cross-run match — the value changes between processes, so the same input misses its own cache. Use `hashlib.sha256`, a normalized slug, or a composite key
- Update state, settings, and manifest files by writing a sibling temp file and replacing atomically (`os.replace`, `mv` on the same filesystem) — an in-place truncating `open(..., "w")` leaves a half-written file behind when the process dies mid-write
- A scanner, validator, or linter that inspects a tree must discover recursively (`os.walk`, `Path.rglob`, `find`) — a flat `listdir` silently exempts every subdirectory, and the check reports green on files it never opened
- Fix review findings in the current PR. Defer one to a tracking issue or a follow-up PR only when I have explicitly agreed to that deferral — ask, don't assume
- Filing an issue for work you could do now *is* that deferral, and needs the same agreement. It does not read like one from the inside: filing feels like diligence, the issue is a real artifact, and "tracked rather than dropped" sounds like the responsible answer. The test is not whether the work is recorded, it is whether it is done — an issue for something already in your hands converts a fix into a backlog entry and calls it progress. Having measured the cost and found it small argues for doing it, not for filing it. A dispatched subagent reports that deferral to its parent rather than filing; the parent files, or asks
- When automation fails partway through, make it idempotent and re-runnable rather than adding checkpoint/retry/resume logic
- When a linter (nesting depth, ShellCheck, errexit, bare-refs, file size) flags a file the current change set touched, fix every violation in that file — not only the one that failed the check. Pre-existing violations in a file we already modified are in scope; violations in files we did not touch are not
- A Python or shell source file under `ai/`, `bin/` or `lib/` stays within **600 lines of code** — blank lines, comments and docstrings do not count, so documenting a file never pushes it over. Those are the two languages the counter reads; TypeScript under `ai/pi/extensions*/` is out of scope until it gains a counter. `bin/local/validate-file-size` reports it. There are no exemptions: the files over the cap when the gate landed were split before it became unconditional. The budget is on what a reader has to hold in their head, not on the explanation that makes it holdable: a cap on total lines is a tax on the `ceiling:` markers and fallback comments the rules above require, and the cheapest way under any line budget is to delete the prose and keep the complexity
- Size is a symptom, so read it as one. A file over the line is usually two concerns sharing a module or one owner spelled twice; split it along the seam the code already has — the section banner, the duplicated vocabulary, the second writer — rather than at whatever line reaches the number. Splitting by count spreads one problem across more files and makes it harder to see
- Never delete or move a spec, plan, doc, or test file to get past a failing pre-push or pre-commit check. Fix the underlying violation, or stop and ask — bypassing the check by removing its input silently discards work

## Waiting on Background Work

- Start background work through the harness's own job facility — `run_in_background` under Claude Code, `job_start` under Pi — never with a standalone `&` or `nohup`. A job the harness started reports its own completion and can be read and stopped through it; a shell detached with `&` belongs to nobody, so the only thing left to do with it is poll it and the only way to stop it is to hunt the pid. Every rule below about not waiting depends on the work being able to announce itself, which a detached shell cannot do. Enforced by `claude-bash-guard` under Claude Code and by the `background-guard` extension under Pi
- Redirecting a detached run's output to files in the repo to read it back later is the same mistake wearing a second hat — it litters the worktree with what the job facility would have handed you, and a script with a trail writes a better record than the redirect does
- Never run `sleep` to wait for work that reports its own completion — a background job, a CI run, a deploy. `sleep 295` in front of a job that messages you on exit buys nothing: the message arrives on its own schedule either way, and the wait is spent whether the job took ten seconds or the full five minutes. Enforced by `claude-bash-guard` under Claude Code and by the `sleep-guard` extension under Pi, both at ten seconds and up
- Polling is the same mistake spread over more turns. Re-reading a job's output on a timer costs a tool call each time to learn what the completion message says once, for free
- When you do need to know what a long run is *doing* rather than whether it is done, read the structured record it keeps, not the tail of its console output. For anything in this workbench that is `otto-log show <invocation>` — every workbench script writes a trail of timed, reasoned events, and the stderr you would otherwise tail is the summary line, not the record
- After starting background work, do other work or end the turn. Doing nothing is a valid turn, and there is no need to announce the wait
- In a one-shot session (print/JSON mode, a subagent, `claude -p`) the turn's end is the process's exit, so do not start background work there. Run it in the foreground bounded by the shell tool's own timeout and finish it before ending the turn. Enforced by the Pi guards in json/print mode (even when `job_start` is listed) and by `claude-bash-guard` for Claude Code subagents via `agent_id`
- To *bound* how long something may run, pass the bound to the call that starts it rather than sleeping beside it: a bound passed to the call kills the work; a sleep beside it only delays you, and the two drift apart the moment the work runs long. Check the unit rather than assuming: harnesses differ on seconds versus minutes versus milliseconds for the same-named field, and a bound off by 60x either kills the work early or never fires — Pi's bash `timeout` is seconds, so `15000` is four hours, not fifteen seconds
- Choose the bound by how it kills. `job_start`'s `timeout_minutes` and coreutils `timeout <seconds>` inside the command send SIGTERM first, so git finishes or removes its lock. Pi's bash tool `timeout` SIGKILLs the whole process group at once, and a git write cut off that way strands `index.lock` or `packed-refs.lock` for every later command in the repo. So never make the shell tool's timeout the bound for anything that writes git state or runs a suite: bound it the gentle way, and keep a tool timeout, if any, as a backstop above that. Enforced under Pi by the `bash-timeout-guard` extension, which refuses a bash `timeout` over an hour (a millisecond value) and one under five minutes on `git`, `pr`, a suite or a `bin/local/` script
- A short `sleep` a pipeline genuinely needs — letting a server bind its port before the first request — is not this, and is below the threshold the guards enforce
- A job's completion notice is only as honest as the command's exit status. `npm test > out.txt 2>&1; echo "EXIT=$?"` exits with echo's status, so the notice reads "succeeded" for a suite that failed and the true result is in a file nobody re-reads — a green that is worse than no signal, because it is acted on. Let the runner be the last thing the command does. Enforced by the `exit-status-guard` extension under Pi; see testing.md § Reading a Suite Result
- Believe the notice over your own summary of it. Reporting a run as passing on the strength of a masked status is how a false green reaches the user, and the fix is to re-read the status rather than to caveat the claim
- When nothing reports completion, say so rather than guessing a duration from outside. A tool that finishes silently is a gap in the tool, and the fix is to make it report

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
- When a commit message or PR body cites an issue, file the issue first and cite the number it was given — a `#NNNN` that does not exist yet is a dead link, not a tracker
- Silent fallbacks and defense-in-depth patterns require a comment explaining intent
- Mark deliberate simplifications with a `// ceiling:` comment naming the tradeoff and upgrade trigger — e.g. `// ceiling: global lock, upgrade to per-account locks if throughput matters`
- A trigger is a *condition*, not an intention. Write the clause that says when the shortcut stops being acceptable — one turning on `if`, `once`, `when`, `unless`, or `until`, or an explicit `Upgrade trigger:` sentence. "Upgrade when we get around to it" names nothing and reads as no trigger at all
- When a simplification genuinely has no upgrade path, write `// ceiling-permanent:` and say why the alternative is worse. It is counted apart from the pending ones — inventing a fake threshold to satisfy the gate is the outcome this form exists to prevent
- The marker opens its own comment line and may run to the end of the comment block below it, so the trigger does not have to fit on the first line. `bin/local/validate-ceiling` fails on any `ceiling:` marker with neither a trigger nor the permanent form
- When adding docs, extend existing files rather than creating new ones
- Never edit a `docs/*.md` that carries a "Generated from … by bin/local/compose-docs" banner — edit its `docs/*.src.md`, or the source data behind the include directive
- When adding CLI commands or changing command signatures, update `docs/ai-automation.src.md` and/or `README.md`
