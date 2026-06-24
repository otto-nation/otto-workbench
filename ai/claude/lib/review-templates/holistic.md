You are doing a holistic scan of PR #${pr_number} in ${repo}.

${pr_header}

## All files in this PR
${all_files_formatted}
${preflight_data}
${delta_section}

## Task
Review all files listed above (scan-level — understand purpose and structure, not line-by-line deep review).
Use the pre-collected diff and any file contents above. For files listed under "Files not pre-collected" or "Diffs not pre-collected", read them directly using Read or `git diff`. Only use Read/Bash for files not in the PR for cross-references.
Then assess:
1. Does the PR accomplish what the description and commits claim?
2. Is the scope focused or does it mix unrelated concerns?
3. Are there cross-module design issues (inconsistent patterns, missing integrations, API mismatches)?
4. Flag specific things that detailed per-file reviewers should watch for.

${reviews_section}
${issue_section}
${env_section}

## Output
You MUST use the Write tool to write to: ${holistic_output}
Do NOT use Bash (cat, heredoc, python) to write the file — use the Write tool.
The output file and its directory already exist — do NOT create directories or empty files.
Sections: ## Holistic Assessment, ## Flags for Detailed Review, ## Cross-module Concerns
Do NOT write per-file findings — those come from the detailed review phase.

## Turn budget
You have ${max_turns} turns.${omitted_guidance} Write your assessment file within the first few turns. For files listed under "Files not pre-collected," read only those critical to the holistic assessment.
