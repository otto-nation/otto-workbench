/**
 * The run session: the pi session of one `pr batch` run, opened by
 * `pr batch session RUN_ID` (ai/pi/extensions/README.md, run-session). The
 * handlers live in ./wire.ts; this file only supplies pi-tui's Text, the one
 * value import node cannot load outside a session.
 */

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";
import { wire } from "./wire.ts";

export default function (pi: ExtensionAPI) {
  wire(pi, { env: process.env, block: (text) => new Text(text, 1, 0) });
}
