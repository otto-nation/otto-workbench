# Review: acme/widget#38 — feat: retry the flaky fetch
<!-- date: 2026-10-19 -->
<!-- mode: pr -->
<!-- pr: 38 -->
<!-- head_sha: 9f1c2e3d4b5a69788796a5b4c3d2e1f00a1b2c3d -->
<!-- head_ref: feat/retry -->
<!-- base_ref: main -->
<!-- review_type: incremental -->
<!-- prior_sha: 2cb613ec084559a9590f074eabde546c44b284a3 -->
<!-- prior_date: 2026-10-15 -->
<!-- delta_files: 0 -->
<!-- generator: review 1.42.0 / otto-workbench 1.47.0 (6c990742) -->

## Summary
5 findings. No agent ran — the author has committed nothing since the prior review. Its findings are carried forward below unchanged.

## Agent Failures

| Agent | Reason | Status |
|-------|--------|--------|
| disprove | agent completed (subtype=success) but did not write output | failed |

Run `pr review --recover` to retry failed agents.

## Must fix
(none)

## Should fix
- **[S1]** **`apps/server/src/voice/transcriber.ts:343`** — The ENOENT retry clears `buildPromise` with no guard, so concurrent requests each start a build.

- **[S2]** **`apps/web/src/stores/voice.ts:131`** — The `finally` in `stopRecording` resets `recording` even after a cancel from another owner. See [S1] for the server half.

## Nit
- **[N1]** **`apps/desktop/main.js:354`** — `requestingUrl` is passed an origin.
- **[N2]** **`apps/server/src/voice/transcriber.test.ts:88`** — The download-cap test depends on test order.

## Idioms
- **[I1]** `apps/web/src/stores/voice.ts` — `loadStatus` uses a module-level guard, consistent with the cache guidance.

## Verdict
Needs discussion — 2 should-fix, 2 nit, 1 idiom (mechanically merged, not synthesized).
