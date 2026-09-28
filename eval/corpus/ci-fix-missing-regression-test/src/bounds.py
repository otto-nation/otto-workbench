"""Range membership with inclusive endpoints."""


def in_range(n, lo, hi):
    """True iff lo <= n <= hi (inclusive on both ends)."""
    return lo <= n <= hi
