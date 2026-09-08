"""Shared directory-skip set for repo-wide directory walks.

These are directories that hold scratch content, vendored output, or other
non-source material and should never be descended into by a validator that
discovers scripts by walking the whole repo tree.
"""

BASE_SKIP_DIRS = frozenset({'.git', 'node_modules', 'ignore', '.superpowers', 'corpus'})
