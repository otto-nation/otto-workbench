Self-review angle scan of changes on branch ${branch_name} in ${repo}.

${pr_header}
${holistic_block}
${preflight_data}
${delta_section}
${env_section}

## Task

Run each of the following 7 review angles against the diff. For each angle, scan every hunk and produce findings only when you can name a concrete failure scenario.

### Angle A — Line-by-line diff scan
Read every hunk in the diff, line by line. Then Read the enclosing function for each hunk — bugs in unchanged lines of a touched function are in scope. For every line ask: what input, state, timing, or platform makes this line wrong? Look for inverted/wrong conditions, off-by-one, null/undefined deref, missing `await`, falsy-zero checks, wrong-variable copy-paste, error swallowed in catch, unescaped regex metachars.

### Angle B — Removed-behavior auditor
For every line the diff DELETES or replaces, name the invariant or behavior it enforced, then search the new code for where that invariant is re-established. If you can't find it, that's a candidate: a removed guard, a dropped error path, a narrowed validation, a deleted test that was covering a real case.

### Angle C — Cross-file tracer
For each function the diff changes, find its callers (Grep for the symbol) and check whether the change breaks any call site: a new precondition, a changed return shape, a new exception, a timing/ordering dependency. Also check callees: does a parallel change in the same PR make a call unsafe?

### Angle D — Reuse & convention fitness
Flag new code that re-implements something the codebase already has. Grep shared/utility modules, base classes, and files adjacent to the change. Name the existing implementation and cite its location. Also check the inverse: if the existing implementation is itself an anti-pattern (stale API, accidental boilerplate, scale-broken switch/case), flag the convention as a should-fix rather than enforcing conformity — recommend the direction for improvement.

Prefix each finding with a tag:
- `stdlib:` — hand-rolled equivalent of a stdlib or language built-in
- `yagni:` — single-implementation abstraction, unused config, speculative flexibility
- `native:` — platform feature available that the code re-implements

### Angle E — Simplification
Flag unnecessary complexity the diff adds: redundant or derivable state, copy-paste with slight variation, deep nesting, dead code left behind. Name the simpler form that does the same job.

Prefix each finding with a tag:
- `delete:` — dead code, unused flexibility, unreachable paths
- `shrink:` — same logic achievable in fewer lines

End the Simplification angle with `net: -N lines possible` if reductions were found.

### Angle F — Efficiency
Flag wasted work the diff introduces: redundant computation or repeated I/O, independent operations run sequentially, blocking work added to startup or hot paths. Name the cheaper alternative.

### Angle G — Altitude
Check that each change is implemented at the right depth, not as a fragile bandaid. Special cases layered on shared infrastructure are a sign the fix isn't deep enough — prefer generalizing the underlying mechanism over adding special cases.

## Verification

For each candidate finding, verify before including:
- **CONFIRMED**: constructive proof from the code that this is a real issue
- **PLAUSIBLE**: realistic failure path exists (concurrency races, nil on a rare path, off-by-one on a reachable boundary, falsy-zero treated as missing) — include these
- **REFUTED**: factually wrong (quote the actual line), provably impossible (type/constant/invariant), or already handled in this diff — drop these

Do NOT refute a candidate for being "speculative" when the state is realistic.

## Output

You MUST use the Write tool to write findings to: ${angles_output}
Do NOT use Bash (cat, heredoc, python) to write the file — use the Write tool.
The output file and its directory already exist — do NOT create directories or empty files.

Format: ## Must fix / ## Should fix / ## Nit / ## Idioms sections only.
Do NOT write a Summary, File Triage, or Verdict section.

Use finding IDs starting at [M1], [S1], [N1], [I1] — they will be renumbered during merge.
Finding format: `- **[M1]** **\`<file>:<line>\`** — <finding>` — always wrap the file path in backticks inside the bold markers.
Must-fix and should-fix findings must include an evidence block — a blockquoted, fenced code snippet from the referenced file proving the claim. Nit and idiom findings do not need evidence.

Classify findings by severity:
- **Must fix (M)**: Correctness bugs from Angles A, B, C — real defects with concrete failure scenarios
- **Should fix (S)**: Reuse (D), altitude (G), and `delete:` simplification (E) findings — not bugs, but the code should be improved
- **Nit (N)**: `shrink:` simplification (E) and efficiency (F) findings — cleanup and optimization
- **Idioms (I)**: Language or framework idiom violations found by any angle

Omit empty severity sections entirely.

## Turn budget
You have ${max_turns} turns.${omitted_guidance} Write your findings file FIRST based on the diff and file contents — do not investigate before writing. Use remaining turns to verify Must-fix and Should-fix claims against the source and update the file via Edit.

PR branch checked out at: ${wt_path} — you may read files to verify cross-references.
