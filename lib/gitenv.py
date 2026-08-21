"""The inherited git environment, and how to stop it choosing the repository.

The Python half of `lib/gitenv.sh`, for the gates under `bin/local/` that are
Python rather than bash. Same fact, same list: git reads `GIT_DIR` ahead of the
directory `-C` moved to, so a script handed a repository path is answered by
whatever repository the environment names. The pre-push hook exports `GIT_DIR`,
which is precisely when a gate runs.

`tests/gitenv_test.py` fails if the two lists ever drift apart.

```python
subprocess.run(('git', '-C', path, 'status'), env=git_env_clear())
```
"""
import os

GIT_ENV_OVERRIDES = (
    'GIT_DIR',
    'GIT_WORK_TREE',
    'GIT_INDEX_FILE',
    'GIT_OBJECT_DIRECTORY',
    'GIT_ALTERNATE_OBJECT_DIRECTORIES',
)


def git_env_clear(env: dict[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment with every inherited git override dropped.

    Returns a copy rather than mutating `os.environ`, so a caller that only
    wants one subprocess answered honestly does not change the environment of
    everything else it goes on to run. `env` defaults to `os.environ`.
    """
    cleaned = dict(os.environ if env is None else env)
    for name in GIT_ENV_OVERRIDES:
        cleaned.pop(name, None)
    return cleaned
