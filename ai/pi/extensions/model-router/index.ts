import type { ExtensionAPI, ModelRoute } from "@earendil-works/pi-coding-agent";
import {
  ROUTER_ID,
  ROUTER_PROVIDER,
  type RouteMessage,
  type RouterState,
  decide,
  loadTiers,
  planFileTest,
} from "./detect.ts";

/**
 * `workbench/auto`: a virtual model routing each request to one of the
 * workbench's model tiers — `models.session: auto` in config.yml selects it.
 * How it chooses is in route.ts; where the tiers come from is in detect.ts.
 *
 * Registered in every session, at no cost until it is selected: the tiers are
 * read on the first routed request, not at load.
 */
export default function (pi: ExtensionAPI) {
  // One predicate per working directory: resolving the workspace spawns git and
  // resolve-workspace, and a session asks on every request.
  const planTests = new Map<string, (path: string) => boolean>();
  const isPlanFileIn = (cwd: string) => {
    let test = planTests.get(cwd);
    if (!test) {
      test = planFileTest(cwd);
      planTests.set(cwd, test);
    }
    return test;
  };

  pi.registerVirtualModel<RouterState>({
    provider: ROUTER_PROVIDER,
    id: ROUTER_ID,
    name: "Auto (workbench tiers)",
    // The selected level applies to a tier whose model names none of its own.
    // The same vocabulary as a tier's `:level` (core.phases.Thinking).
    thinkingLevels: ["off", "minimal", "low", "medium", "high", "xhigh", "max"],
    route(request, ctx): ModelRoute<RouterState> {
      const decision = decide({
        reason: request.reason,
        state: request.state,
        messages: request.messages as readonly RouteMessage[],
        hasPrevious: request.previous !== undefined,
        hasFailed: request.failed !== undefined,
        isPlanFile: isPlanFileIn(ctx.cwd),
      });
      const kept = decision.keep === "failed" ? request.failed : decision.keep === "previous" ? request.previous : undefined;
      if (kept) {
        return { model: kept.model, thinkingLevel: kept.thinkingLevel ?? request.thinkingLevel, state: decision.state };
      }
      const ref = loadTiers()[decision.tier];
      const model = ctx.modelRegistry.find(ref.provider, ref.id);
      if (!model) {
        throw new Error(
          `models.${decision.tier} is ${ref.provider}/${ref.id}, which pi does not list — ` +
            `set it to a model \`pi --list-models\` shows`,
        );
      }
      // A compaction summary is sent the whole conversation. Light takes it only
      // when its window is no smaller than the model holding the conversation;
      // otherwise that model, which fits it by construction, does the summary.
      const holder = request.previous;
      if (request.reason === "direct" && holder && narrower(model.contextWindow, holder.model.contextWindow)) {
        return { model: holder.model, thinkingLevel: holder.thinkingLevel ?? request.thinkingLevel };
      }
      // The level is one of Thinking's values: sync refuses any other.
      return { model, thinkingLevel: (ref.level ?? request.thinkingLevel) as ModelRoute["thinkingLevel"], state: decision.state };
    },
  });
}

/** Whether a window of CANDIDATE tokens is known to be smaller than one of HOLDER. */
function narrower(candidate: number | undefined, holder: number | undefined): boolean {
  return Boolean(candidate && holder && candidate < holder);
}
