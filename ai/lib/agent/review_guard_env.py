"""Environment the Pi review-guard extension reads.

Split from ``backend_pi`` so the verify-phase flag can live next to the
worktree bounds without growing that module past the code-line cap.

Process writes (bash) bypass the guard, which is why ``fix.containment`` is
the real control on a verify agent.
"""

# doc-group: backend

from __future__ import annotations

import os

import agent.registry
from agent.backend import AgentInvocation, agent_env

ENV_REVIEW_WORKTREE_DIR = "REVIEW_WORKTREE_DIR"
ENV_REVIEW_ALLOWED_DIRS = "REVIEW_ALLOWED_DIRS"
ENV_REVIEW_VERIFY_PHASE = "REVIEW_VERIFY_PHASE"


def apply_guard_env(inv: AgentInvocation) -> dict[str, str]:
    """The subprocess environment, with the review guard's roots added.

    ``REVIEW_VERIFY_PHASE`` is set for every verify gate
    (``agent.registry.VERIFY_PHASES``), so review-guard permits
    writes to the tracking file and other add_dirs outside the worktree, not
    to the worktree itself.
    """
    env = agent_env(inv)
    env[ENV_REVIEW_WORKTREE_DIR] = inv.cwd
    extra = [d for d in inv.add_dirs if d]
    if extra:
        env[ENV_REVIEW_ALLOWED_DIRS] = os.pathsep.join(extra)
    if agent.registry.is_verify_phase(inv.phase):
        env[ENV_REVIEW_VERIFY_PHASE] = "1"
    return env
