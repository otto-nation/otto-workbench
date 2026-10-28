"""Event handling. The counter is only ever touched under _lock."""

import threading

from counter import Counter

_lock = threading.Lock()
counter = Counter()


def handle(event):
    with _lock:
        counter.bump()


def average(values):
    return sum(values) / len(values)


def report(readings):
    good = [r.value for r in readings if r.ok]
    return average(good)
