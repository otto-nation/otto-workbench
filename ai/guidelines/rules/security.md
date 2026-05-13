# Security

## Secrets and Credentials

- Never write secrets, tokens, API keys, passwords, or credentials to any tracked file
- Machine-specific git identity (name, email, signingKey, credential helpers) belongs in `~/.gitconfig` — not in `git/gitconfig.shared` which is committed
- If you need to reference a secret in a script, read it from the environment — never hardcode it
- When editing `git/gitconfig.shared` or any tracked config: only add settings that are safe on any machine
