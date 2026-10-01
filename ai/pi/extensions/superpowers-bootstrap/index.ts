/**
 * Delivers the superpowers bootstrap in the system prompt instead of the
 * transcript. See ./bootstrap.ts for why the upstream delivery breaks signed
 * thinking blocks.
 *
 * The section is set by mutating `systemPromptOptions` rather than returning
 * `systemPrompt`: a returned prompt replaces the whole structured prompt for the
 * run, while a section is diffed and recorded as a delta like every other part
 * of it.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { bootstrapSection, SECTION_NAME } from "./bootstrap.ts";

export default function (pi: ExtensionAPI) {
  pi.on("before_agent_start", async (event) => {
    const section = bootstrapSection(event.systemPromptOptions.skills);
    if (section) event.systemPromptOptions.sections[SECTION_NAME] = section;
  });
}
