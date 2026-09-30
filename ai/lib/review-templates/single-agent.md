Review PR #${pr_number} in ${repo}.

${pr_header}
${state_context}
${preflight_data}
${delta_section}
${reviews_section}
${env_section}

## Output
${output_block}

Write no `<!-- key: value -->` metadata comments. The harness writes the document's metadata header itself once you are done — which commit was reviewed, what it is a delta against, which build produced it — and it knows those facts where you do not.
Format each finding as a list item: `- **[M1]** **\`<file>:<line>\`** — <finding>`. NEVER use ### headings for findings — downstream counters and posting tools parse the `- **[X1]**` list-item format only.
Must-fix and should-fix findings must include an evidence block — a blockquoted, fenced code snippet from the referenced file proving the claim.
A tradeoff the code marks with a `ceiling:` or `ceiling-permanent:` comment is a documented decision, not a defect — do not raise it. Raise it only when the marker's own upgrade trigger has already fired, and say which trigger and what fired it.
A finding already annotated `*(declined — reason)*` was adjudicated — carry it forward with the annotation intact rather than re-raising it as open.
Include a `## Verdict` section: ${verdict_options}. Disapprove means the overall approach is wrong and the PR should not land in any form — explain what should be done instead.

## Turn budget
You have ${max_turns} turns (each turn can include multiple parallel tool calls).${omitted_guidance} Write a complete review file FIRST based on the diff and file contents — do not investigate before that first write. Further complete rewrites are expected as findings accumulate; never leave the file as a non-document. Use remaining turns to verify Must-fix and Should-fix claims against the source and rewrite the file via Edit. Batch independent lookups (e.g. multiple grep/find/read calls) into a single turn.

A scratch file is not the review file. Writing a probe script, extracting a
dependency's source, or redirecting output somewhere to read it back leaves the
review file empty, and a run that ends there — out of turns, or because you
decided you were done — reports nothing at all. If a claim needs a shell to
settle and you have not written the file yet, write the file with the claim
marked unverified and settle it afterwards.

Never write that you ran something unless you ran it in this session. "All
five pass locally", "I ran the suite", "verified by running" and the like are
claims a reader acts on without re-checking, and the sequence above makes them
easy to write by accident: the first write happens before any investigation,
so a claim drafted there describes a command that has not executed and may
never. Say what the code shows, or mark the claim unverified — the two honest
options.

This holds for every severity, including nits and idioms. Only Must-fix and
Should-fix findings have their evidence checked against the tree, so a claim
in a nit or idiom is one no later gate will catch.
${issue_section}
${prior_section}
${reply_threads}
