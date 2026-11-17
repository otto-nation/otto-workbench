/**
 * The session brief's handlers, kept out of index.ts so a test can hand them a
 * stand-in `pi` (tests/pi_session_brief_fake.mjs): this file imports SDK types
 * only. What a brief is and when it is written: the `session-brief` section of
 * ../README.md.
 */

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  BRIEF_TYPE,
  EMPTY_FACTS,
  IDLE_MS,
  briefContext,
  briefData,
  clearArgv,
  factsArgv,
  latestBrief,
  lightTierRef,
  liveArgv,
  needsBrief,
  parseBrief,
  parseFacts,
  pickModel,
  replyText,
  shouldBrief,
  shouldRename,
  transcript,
} from "./detect.ts";

export interface Deps {
  /** The environment the session started with; index.ts passes process.env. */
  env: Record<string, string | undefined>;
  /** This pi's pid, the live marker's key. */
  pid: number;
  now: () => Date;
  setTimer: (fn: () => unknown, ms: number) => unknown;
  clearTimer: (handle: unknown) => void;
  /** `config models` output, or null (detect.ts readTiersText). */
  tiersText: () => string | null;
}

export const BRIEF_MAX_TOKENS = 400;
export const FACTS_TIMEOUT_MS = 30_000;
export const LIVE_TIMEOUT_MS = 10_000;
/** How long a clean exit may wait on the final brief before quitting anyway. */
export const SHUTDOWN_BRIEF_MS = 20_000;

const FAILED = { stdout: "", stderr: "", code: 1, killed: false };

export function wire(pi: ExtensionAPI, deps: Deps): void {
  let timer: unknown = null;
  let busy = false;
  let inflight: Promise<void> | null = null;

  const cancel = () => {
    if (timer !== null) {
      deps.clearTimer(timer);
      timer = null;
    }
  };

  const piSessions = (args: string[], timeout: number, signal?: AbortSignal) =>
    pi.exec("python3", args, { timeout, signal }).catch(() => FAILED);

  function refresh(ctx: ExtensionContext, signal?: AbortSignal): Promise<void> {
    const run = write(ctx, signal);
    const tracked: Promise<void> = run.finally(() => {
      if (inflight === tracked) inflight = null;
    });
    inflight = tracked;
    return run;
  }

  async function write(ctx: ExtensionContext, signal?: AbortSignal): Promise<void> {
    const file = ctx.sessionManager.getSessionFile();
    const entries = ctx.sessionManager.getBranch();
    if (busy || !file || !needsBrief(entries)) return;
    busy = true;
    try {
      const ref = lightTierRef(deps.tiersText());
      const model = ref ? pickModel(ctx.modelRegistry, ref, ctx.model) : undefined;
      if (!model) return;
      const reply = await ctx.modelRegistry.complete(
        model, briefContext(transcript(entries), deps.now().getTime()), { maxTokens: BRIEF_MAX_TOKENS, signal },
      );
      if (reply.stopReason === "error" || reply.stopReason === "aborted") return;
      const brief = parseBrief(replyText(reply.content));
      if (!brief) return;
      const ran = await piSessions(factsArgv(file), FACTS_TIMEOUT_MS, signal);
      const facts = ran.code === 0 ? parseFacts(ran.stdout) : EMPTY_FACTS;
      let titleSet = latestBrief(entries)?.title_set ?? null;
      if (shouldRename(pi.getSessionName(), titleSet)) {
        pi.setSessionName(brief.title);
        titleSet = brief.title;
      }
      pi.appendEntry(BRIEF_TYPE, briefData({
        titleSet, name: pi.getSessionName() ?? null, tldr: brief.tldr, facts, at: deps.now(),
      }));
    } catch {
      // Best-effort: a provider or exec failure leaves the last brief in place, and
      // needsBrief stays true, so the next idle tries again.
    } finally {
      busy = false;
    }
  }

  pi.on("session_start", async (_event, ctx) => {
    cancel();
    if (!shouldBrief(ctx.mode)) return;
    const file = ctx.sessionManager.getSessionFile();
    // Unawaited: the marker is for the Sessions panel, and startup must not wait on Python.
    if (file) void piSessions(liveArgv(deps.pid, file), LIVE_TIMEOUT_MS);
  });

  pi.on("input", async () => {
    cancel();
  });

  pi.on("agent_start", async () => {
    cancel();
  });

  pi.on("agent_settled", async (_event, ctx) => {
    cancel();
    if (!shouldBrief(ctx.mode)) return;
    timer = deps.setTimer(() => {
      timer = null;
      return refresh(ctx);
    }, IDLE_MS);
  });

  pi.on("session_shutdown", async (event, ctx) => {
    cancel();
    if (!shouldBrief(ctx.mode)) return;
    // reload keeps the same session running; every other reason ends it here (T3-a).
    if (event.reason !== "reload") {
      // An idle brief already in flight would make refresh skip on busy: wait for it
      // (bounded), then brief whatever it left unbriefed.
      const signal = AbortSignal.timeout(SHUTDOWN_BRIEF_MS);
      if (inflight) {
        await Promise.race([inflight, new Promise((r) => signal.addEventListener("abort", r, { once: true }))]);
      }
      await refresh(ctx, signal);
    }
    await piSessions(clearArgv(deps.pid), LIVE_TIMEOUT_MS);
  });
}
