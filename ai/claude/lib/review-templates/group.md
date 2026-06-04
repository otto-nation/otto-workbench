Review PR #${pr_number} in ${repo} — GROUP ${group_idx}/${group_count}: ${group_name}

${pr_header}
${holistic_block}

## Scope constraint
Review ONLY the following files. Do NOT read or comment on files outside this list:
${group_files_formatted}
${preflight_data}

Prior reviews exist and will be consulted during synthesis. Focus only on your assigned files.
${issue_section}

${env_section}

## Output
Write findings to: ${group_output}
Format: ## File Triage section + ## Must fix / ## Should fix / ## Nit / ## Idioms sections only.
Do NOT write a Summary or Verdict — those will be added in a synthesis step.
Use finding IDs starting at [M1], [S1], [N1], [I1] — they will be renumbered during merge.
Finding format: `- **[M1]** **\`<file>:<line>\`** — <finding>` — always wrap the file path in backticks inside the bold markers.
Must-fix and should-fix findings must include an evidence block — a blockquoted, fenced code snippet from the referenced file proving the claim. Nit and idiom findings do not need evidence.
Skip or downgrade to Nit any findings in generated files (e.g. `*_pb2.py`, `*.pb.go`, `*.pb.gw.go`, `*_pb2_grpc.py`). Generated code is not author-controlled — only flag it if a proto source change is needed.

## Turn budget
You have a limited number of tool calls. Write your findings file FIRST based on the pre-collected diff and file contents — do not investigate before writing. Use any remaining turns to verify specific concerns and update the file.
${prior_section}
