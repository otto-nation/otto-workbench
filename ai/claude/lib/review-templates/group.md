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
${prior_section}
