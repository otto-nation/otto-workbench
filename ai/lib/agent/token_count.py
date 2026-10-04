"""Exact input-token counts for a prompt, where the platform can give them.

A byte count cannot bound a token count: the same 480KB is 242k tokens of
ordinary review prose and 457k of base64, and only the content decides which.
This module is how a caller stops guessing — it asks the model's own tokenizer
what a string costs, and says plainly when nothing can answer.

`count_tokens` returns `None` rather than an estimate when no counter is
reachable, because a caller that cannot tell a measurement from a guess will
spend a guess as though it were a measurement. Every reason for that `None` is
ordinary: a machine on the first-party API has no Vertex credentials, and a
model shorthand never resolves to something the endpoint accepts.

Two properties of the endpoint are load-bearing and easy to get wrong. It
counts the system prompt and tool schemas when they are passed, which is how
the tokens a prompt is charged beyond its own text become measurable rather
than reserved-for. And it is tokenizer-specific: `claude-sonnet-5` counts the
same text ~27% denser than `claude-sonnet-4-5`, so a count is only meaningful
against the model that will actually serve the request.

The `system` and `tools` arguments exist for that first property and no caller
supplies them yet. `claude -p` assembles both inside the CLI, so a review has
no handle on the text its agent will actually be sent; a count taken here is
the prompt alone. A real request runs well above it, by an amount that differs
per backend; `review.budget` owns the measured figures and the reserve built
from them (see the comment above `OVERHEAD_RESERVE_TOKENS`). A caller comparing
a count against a context window owes itself that margin until the two are
wired together.

The review pipeline records each count in `prompt-stats.json` (see
`review.prompt`). That write, the `WORKBENCH_AI_MEASURE_TOKENS=0` opt-out, and
the measured round-trip latency live there, not here: this module only asks the
endpoint and returns `None` when it cannot.
"""

# doc-group: backend

from __future__ import annotations

import json
import urllib.error
import urllib.request

import core.log
import core.timeouts
from agent.vertex_quota import access_token, is_checkable, vertex_env

# The endpoint speaks the Anthropic message schema through Vertex's rawPredict
# shim, which pins its own version string rather than the API's date header.
_ANTHROPIC_VERSION = "vertex-2023-10-16"

# `count-tokens` is a pseudo-model on the publisher path: the model whose
# tokenizer is wanted travels in the body, not the URL.
_COUNT_TOKENS_MODEL = "count-tokens"

# Vertex multi-regions are not `{region}-aiplatform.googleapis.com` — that
# host 404s with HTML. They live on the `.rep.` publisher frontend instead.
_MULTI_REGIONS = frozenset({"us", "eu"})


def aiplatform_host(region: str) -> str:
    """The Vertex AI host for ``region``.

    `global` is the un-prefixed producer. Multi-regions (`us`, `eu`) use the
    `.rep.` frontend (`aiplatform.us.rep.googleapis.com`); a single region
    such as `us-east5` keeps `{region}-aiplatform.googleapis.com`.
    """
    if region == "global":
        return "https://aiplatform.googleapis.com"
    if region in _MULTI_REGIONS:
        return f"https://aiplatform.{region}.rep.googleapis.com"
    return f"https://{region}-aiplatform.googleapis.com"


def _endpoint(project: str, region: str) -> str:
    return (
        f"{aiplatform_host(region)}/v1/projects/{project}/locations/{region}"
        f"/publishers/anthropic/models/{_COUNT_TOKENS_MODEL}:rawPredict"
    )


def _count_failure_note(exc: BaseException) -> str:
    """Why a count did not come back, for the dim log.

    HTTP errors are named by status: catching them as `URLError` (their
    parent) used to report a transport class and "using estimate", which
    was two lies — the host answered, and nothing estimated anything.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return type(exc).__name__


def count_tokens(
    text: str,
    model: str,
    *,
    system: str = "",
    tools: list[dict] | None = None,
) -> int | None:
    """The exact input tokens `text` costs `model`, or None if nothing can say.

    `model` must be a concrete id — a CLI shorthand like "sonnet" is rejected
    by the endpoint, so it is refused here instead of spending a round trip to
    learn it. `system` and `tools` are counted alongside the text when given,
    which is what makes the non-prompt overhead a measurement rather than a
    reserve; omitting them counts the text alone, which is less than the
    request will cost.

    Returns None for every condition that leaves the answer unknown: not on
    Vertex, no credentials, a shorthand model, a transport error. None is not
    zero and not "it fits" — a caller decides for itself what to do without a
    count, and the one thing it must not do is treat the absence as a pass.
    """
    if not is_checkable(model):
        return None

    env = vertex_env()
    if not env:
        return None
    project, region = env

    token = access_token()
    if not token:
        return None

    payload: dict = {
        "anthropic_version": _ANTHROPIC_VERSION,
        "model": model,
        "messages": [{"role": "user", "content": text}],
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools

    req = urllib.request.Request(
        _endpoint(project, region),
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=core.timeouts.NETWORK) as resp:
            return json.loads(resp.read())["input_tokens"]
    except (
        # HTTPError subclasses URLError; it is listed anyway because
        # `_count_failure_note` reads its status code, so keep it explicit.
        urllib.error.HTTPError,
        urllib.error.URLError,
        OSError,
        json.JSONDecodeError,
        KeyError,
    ) as exc:
        # Dim rather than warn: a run whose prompt cannot be counted still
        # proceeds, and this is the note that says the count was skipped —
        # not a failure of the review, and not an estimate of anything.
        core.log.dim(
            f"Token count unavailable ({_count_failure_note(exc)}) — count skipped"
        )
        return None
