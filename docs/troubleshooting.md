# Troubleshooting

Common issues and how to resolve them.

## "command not found: otto-workbench"

`~/.local/bin` is not in your `PATH`. This is added automatically by `install.sh`, but requires a shell reload:

```bash
exec zsh
```

If it's still missing, check that `~/.zshrc` contains a `PATH` entry for `~/.local/bin`.

## "bash 4.3+ required"

macOS ships with bash 3.2 (due to licensing). Install a modern version:

```bash
brew install bash
```

The workbench scripts use `#!/usr/bin/env bash` to pick up Homebrew's version automatically.

## Files "skipped (real file exists)" during sync

`otto-workbench sync` never silently overwrites real files — it warns and skips them. This protects your manual edits.

To resolve: back up the blocking file, remove it, then re-sync:

```bash
mv ~/.config/zsh/config.d/some-file ~/.config/zsh/config.d/some-file.bak
otto-workbench sync
```

For interactive overwrite prompts (overwrite/backup/skip), use `install.sh` instead.

## "tools.generated.md is out of date" (pre-push failure)

The pre-push hook detected that generated files don't match their sources. Regenerate and commit:

```bash
generate-tool-context
generate-git-rules
git add ai/guidelines/rules/tools.generated.md ai/guidelines/rules/git.generated.md
git commit -m "chore: regenerate tool context"
```

## "yq not found"

Several workbench scripts depend on `yq` for YAML processing:

```bash
brew install yq
```

## AI setup: "AI_COMMAND not configured"

The global Taskfile needs an AI tool configured. Run:

```bash
task --global ai:setup
```

This creates `~/.config/task/taskfile.env` and prompts you for `AI_COMMAND`, `GH_TOKEN`, and optionally `ANTHROPIC_API_KEY`.

## "Cannot connect to the Docker daemon"

Start your Docker runtime:

```bash
# If using Colima:
colima start

# If using OrbStack:
# Launch OrbStack from Applications
```

The workbench detects your active runtime from the `~/.docker/run/docker.sock` symlink target — it doesn't matter which runtime is installed, only which is running.

## Shell changes not taking effect

ZSH configuration is copied (not symlinked) to `~/.config/zsh/config.d/`. After pulling workbench updates:

```bash
otto-workbench sync   # re-copies config files
exec zsh              # reloads the shell
```

## Git hooks not running

Hooks need to be activated once per clone:

```bash
task dev:setup
```

This sets `core.hooksPath` to point to the workbench's [`git/hooks/`](../git/hooks/) directory.

## "merge conflict" in `~/.claude/settings.json`

The AI sync merges `settings.json` rather than overwriting. If you see unexpected values, re-sync:

```bash
otto-workbench claude
```

This preserves your additions while applying workbench defaults. If the file is corrupted, delete it and re-sync — the workbench will recreate it from its template.
