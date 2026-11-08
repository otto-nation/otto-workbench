"""Shared test doubles for the fix-suite tests."""


class _RecordingTrail:
    """A trail that keeps what it was told, so a test can read it back."""

    def __init__(self):
        self.events = []

    def info(self, action, detail, data=None):
        self.events.append(("info", action, detail, data or {}))

    def warn(self, action, detail, data=None):
        self.events.append(("warn", action, detail, data or {}))

    def error(self, action, detail, data=None):
        self.events.append(("error", action, detail, data or {}))

    def failure(self, action, detail, **kwargs):
        self.events.append(("failure", action, detail, kwargs))
        return None
