${holistic_block}
${project_context}
${issue_section}

${env_section}

## Your Assignment — GROUP ${group_idx}/${group_count}: ${group_name}

Review PR #${pr_number} in ${repo}.
${pr_header}

Review ONLY the following files. Do NOT read or comment on files outside this list.
As you review, watch for: suspicious deletions or silent behavior changes, unhandled error/edge cases, missing sibling updates (e.g., enum added but switch not updated), security concerns (injection, auth bypass, data exposure), and correctness issues (off-by-one, race conditions, null handling).
${group_files_formatted}
${preflight_data}
${delta_section}
${prior_section}
${reply_threads}

## Output
${output_block}

Use finding IDs starting at M1, S1, N1, I1 — they will be renumbered during merge.
Do NOT write a summary or a verdict — those are added in a synthesis step.
Must-fix and should-fix findings must carry `evidence`: the lines of the referenced file that prove the claim, copied verbatim. Nit and idiom findings do not need evidence.
Skip or downgrade to a nit any finding in generated files (e.g. `*_pb2.py`, `*.pb.go`, `*.pb.gw.go`, `*_pb2_grpc.py`). Generated code is not author-controlled — only flag it if a proto source change is needed.
A tradeoff the code marks with a `ceiling:` or `ceiling-permanent:` comment is a documented decision, not a defect — do not raise it. Raise it only when the marker's own upgrade trigger has already fired, and say which trigger and what fired it.
On a re-review, a prior finding marked `*(declined — reason)*` was adjudicated — carry it forward with `sid` set to the value in its `<!-- sid: … -->` marker and `declined` set to that reason, rather than re-raising it as open.

## Turn budget
You have ${max_turns} turns.${omitted_guidance} Write a complete findings file FIRST based on the diff and file contents — do not investigate before that first write. Further complete rewrites are expected as findings accumulate; never leave the file as a non-document. Use remaining turns to verify specific concerns and rewrite the file.

${execution_claim_guard}
