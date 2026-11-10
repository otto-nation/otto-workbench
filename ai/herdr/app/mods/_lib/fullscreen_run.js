/**
 * Owner of the PRs panel enter/exit full-screen sequence. Cannot be loaded by
 * node tests because it imports herdr.
 */
import { run, shared, setShared, layout } from "herdr";
import { enterPlan, exitSteps } from "./fullscreen.js";

export function enterFullscreen() {
  const plan = enterPlan(layout());
  if (plan.savedPx !== null) setShared("prs.panelPx", plan.savedPx);
  for (const step of plan.steps) run(step.command, step.args);
}

export function exitFullscreen() {
  for (const step of exitSteps(shared("prs.panelPx"))) run(step.command, step.args);
  setShared("prs.panelPx", null);
}
