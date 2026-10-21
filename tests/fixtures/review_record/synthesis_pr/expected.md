# Review: acme/widget#38 — feat: add on-device dictation to the composer

<!-- date: 2026-10-15 -->
<!-- mode: pr -->
<!-- pr: 38 -->
<!-- head_sha: 2cb613ec084559a9590f074eabde546c44b284a3 -->
<!-- head_ref: add-voice-chat -->
<!-- base_ref: main -->
<!-- review_type: full -->
<!-- status: partial -->
<!-- generator: review 1.42.0 / otto-workbench 1.47.0 (a1350c77) -->
<!-- rendered from review.json — edits here are overwritten; change the record -->

## File Triage

- `apps/desktop/package.json` — reviewed; entitlements config added.
- `packages/shared/src/index.ts` — reviewed; types and constant only.

## Agent Failures

| Agent | Reason | Status |
|-------|--------|--------|
| disprove | agent completed (subtype=success) but did not write output | failed |

Run `pr review --recover` to retry failed agents.

## Summary

Adds speech-to-text in the composer behind a mic button. The endpoint is hardened for cross-origin use. The remaining findings are robustness issues; none blocks.

## Must fix

(none)

## Should fix

- **[S1]** `apps/server/src/voice/transcriber.ts:343` — The ENOENT retry clears `buildPromise` with no guard, so concurrent requests each start a build.
- **[S2]** `apps/web/src/stores/voice.ts:131` — The `finally` in `stopRecording` resets `recording` even after a cancel from another owner. See [S1] for the server half.

## Nit

- **[N1]** `apps/desktop/main.js:354` — `requestingUrl` is passed an origin.
- **[N2]** `apps/server/src/voice/transcriber.test.ts:88` — The download-cap test depends on test order.

## Idioms

- **[I1]** `apps/web/src/stores/voice.ts` — `loadStatus` uses a module-level guard, consistent with the cache guidance.

## Verdict

**Approve.** There are no must-fix issues; the should-fix items can follow up.
