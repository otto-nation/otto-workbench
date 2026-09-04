"""What a fix pass is handed, in terms no one domain owns.

`pr.fix` says what became of an item; this says what the item was. The two sit
either side of the agent: a domain fetches its own work — reviewer threads, CI
failures, review findings — and turns each unit of it into a
:class:`FixItem`, the agent is handed those, and what comes back is an
:class:`~pr.fix.ItemOutcome` per id.

A `FixItem` carries only what the tracking file needs to render a section and
key it back: an id, where the work is, a one-line label, and the body the
domain rendered. A reviewer login, a CI job name and a finding's severity are
the domain's own, and stay on the domain's own item type — the same argument
`ItemOutcome` already makes for the outcome side. What crosses this boundary is
what every domain can answer.

Like `pr.fix`, this sits below the domains: it imports the standard library and
nothing else from ``ai/lib``, so the shared fix machinery can depend on it
without pulling a review or comments layer in behind it.
"""

# doc-group: pr-state

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FixItem:
    """One unit of work a fix pass hands to an agent.

    `id` is the only required field in practice: it is what the tracking file
    marks each section with and what the parse keys its outcomes by, so an item
    without one cannot be reported on. The rest is presentation — `file`, `line`
    and `label` compose the section heading, and `body` is whatever context the
    domain decided the agent needs, already rendered as markdown.
    """

    id: str = ""
    file: str = ""
    line: int = 0
    # The trailing half of the section heading — who asked, or what failed.
    label: str = ""
    body: str = ""

    def __post_init__(self) -> None:
        self.line = int(self.line or 0)

    def location(self) -> str:
        """Where the work is, as the heading spells it.

        A file with no line is still a location; neither is the em dash, which
        stands in so a section heading keeps its shape when the domain has no
        path to give at all.
        """
        if self.file and self.line:
            return f"{self.file}:{self.line}"
        return self.file or "—"
