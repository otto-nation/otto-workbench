"""Layer 3 — memory consolidation scans. May import: core, config.

The bodies of `dream-scan` and `promote-scan`: the reports the dream and
promote skills read. The store itself (where memory lives, repo keys,
topic-file parsing, gate stamps) is `core.memory`'s, and transcripts are
`core.sessions`'s. Layer 3, not 2, because both scans read the project
registry from `config`, which is layer 2.
"""
