You are doing a holistic scan of PR #${pr_number} in ${repo}.

${pr_header}

## All files in this PR
${all_files_formatted}

## Task
Read EVERY file listed above (scan-level — understand purpose and structure, not line-by-line deep review).
Then assess:
1. Does the PR accomplish what the description and commits claim?
2. Is the scope focused or does it mix unrelated concerns?
3. Are there cross-module design issues (inconsistent patterns, missing integrations, API mismatches)?
4. Flag specific things that detailed per-file reviewers should watch for.

${reviews_section}
${issue_section}
${env_section}

## Output
Write to: ${holistic_output}
Sections: ## Holistic Assessment, ## Flags for Detailed Review, ## Cross-module Concerns
Do NOT write per-file findings — those come from the detailed review phase.
