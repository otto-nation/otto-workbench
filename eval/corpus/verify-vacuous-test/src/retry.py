import time


def retry(fn, attempts=3, delay=1.0):
    last = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as exc:
            last = exc
    raise last
