# Linear

- When creating Linear issues, describe the problem and desired outcome — not implementation details. Structure as: Context (what prompted this), Problem (what's wrong), Goal (desired outcome). Keep specific files, code snippets, and implementation steps in the plan, not the issue.
- Linear issue titles should follow conventional commit format: `type(scope): subject` — same rules as git commit headers (72 chars, allowed types, no period). This keeps issues, commits, and PRs consistent.
- When an issue's scope expands beyond its original title/description, retitle and rewrite the description to reflect the broader scope. If the original scope was a meaningful unit of work, preserve it as a sub-issue rather than losing that context.
- Prefer sub-issues over expanding a single issue's scope when the work has distinct deliverables. A parent issue should describe the initiative; sub-issues should each be independently completable. Don't nest sub-issues more than one level.
