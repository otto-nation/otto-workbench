/**
 * Pure model and CLI helpers for the Herdr Settings pane (global models.* + ai sync).
 * Python (`otto-workbench config models` and `pr models --json`) owns every fact;
 * this module builds argv, parses JSON, formats options/preview rows, and wraps invoke.
 * Imports nothing from `herdr` so node tests load it directly.
 */

import { invoke, readJson } from "./board_data.js";
import { FLOOR_REASON, parseChoices, SECTION_ENABLED, SECTION_TIERS } from "./model_choices.js";

export const SETTINGS_TARGETS = Object.freeze(["session", "deep", "standard", "light"]);

export const TARGET_LABEL = Object.freeze({
  session: "Session",
  deep: "Deep",
  standard: "Standard",
  light: "Light",
});

export const AUTO_REF = "auto";
export const AUTO_LABEL = "Auto (workbench tiers)";
export const UNSET_LABEL = "Not set";
export const SECTION_SESSION = "Routing";

export const SYNC_CAPTION = "Pi and Claude Code pick this up after Sync";
export const CLEAN_REASON = "No unsynced changes";
export const OUTDATED_TEXT = "Update this machine to manage settings (otto-workbench sync)";

export const CHOICES_TIMEOUT_MS = 25_000;
export const SYNC_TIMEOUT_MS = 120_000;

const TIER_TARGETS = new Set(["deep", "standard", "light"]);

function isObject(v) {
  return v != null && typeof v === "object" && !Array.isArray(v);
}

function fold(s) {
  return String(s ?? "").toLowerCase();
}

function matches(query, ...parts) {
  const q = fold(query).trim();
  if (!q) return true;
  return parts.some((p) => fold(p).includes(q));
}

export function configModelsArgv() {
  return ["config", "models"];
}

export function writeTargetArgv(target, value) {
  const trimmed = String(value ?? "").trim();
  // A leading dash would be read by the CLI as a flag, not a model id.
  if (!trimmed || trimmed.startsWith("-")) return null;
  if (target === "session") return ["config", "set", "models.session", trimmed];
  if (TIER_TARGETS.has(target)) return ["config", "model", target, trimmed];
  throw new Error(`unknown settings target: ${target}`);
}

export function syncArgv() {
  return ["ai", "sync"];
}

export function parseConfigModels(value) {
  if (!isObject(value) || !isObject(value.claude) || !isObject(value.pi) || !isObject(value.tiers)) {
    return { ok: false, error: { kind: "bad-json", message: "invalid config models document" } };
  }
  return { ok: true, doc: value };
}

export function formatModelRef(entry) {
  if (!isObject(entry) || !entry.id) return "";
  const base = entry.provider ? `${entry.provider}/${entry.id}` : String(entry.id);
  return entry.level ? `${base}:${entry.level}` : base;
}

function sessionRef(pi) {
  if (!isObject(pi) || !pi.defaultModel) return "";
  if (
    (pi.defaultProvider === "workbench" && pi.defaultModel === "auto")
    || pi.defaultModel === "workbench/auto"
  ) {
    return AUTO_REF;
  }
  const base = pi.defaultProvider ? `${pi.defaultProvider}/${pi.defaultModel}` : String(pi.defaultModel);
  return pi.defaultThinkingLevel ? `${base}:${pi.defaultThinkingLevel}` : base;
}

export function currentValues(doc) {
  return {
    session: sessionRef(doc?.pi),
    deep: formatModelRef(doc?.tiers?.deep),
    standard: formatModelRef(doc?.tiers?.standard),
    light: formatModelRef(doc?.tiers?.light),
  };
}

export function displayValue(ref) {
  const r = String(ref ?? "").trim();
  if (!r) return UNSET_LABEL;
  if (r === AUTO_REF) return AUTO_LABEL;
  return r;
}

function reviewOkLookup(doc, ref) {
  for (const info of Object.values(doc.tiers ?? {})) {
    if (info?.ref === ref && typeof info.review_ok === "boolean") return info.review_ok;
  }
  for (const provider of doc.catalogue?.providers ?? []) {
    for (const model of provider.models ?? []) {
      if (model?.ref === ref && typeof model.review_ok === "boolean") return model.review_ok;
    }
  }
  return true;
}

function optionRow({ ref, label, reviewOk, floor, current }) {
  const disabled = Boolean(floor) && reviewOk === false;
  return {
    ref,
    label,
    selected: String(current ?? "") === String(ref ?? ""),
    disabled,
    reason: disabled ? FLOOR_REASON : null,
  };
}

function pushSection(sections, label, rows) {
  if (rows.length > 0) sections.push({ label, rows });
}

export function targetOptions(choicesDoc, target, query, current = "") {
  const parsed = parseChoices(choicesDoc);
  if (!parsed.ok) return { ok: false, error: parsed.error, catalogueError: null, sections: [] };
  const d = parsed.doc;
  const floor = target === "deep" || target === "standard";
  const sections = [];

  if (target === "session") {
    const routing = [];
    if (matches(query, AUTO_LABEL, AUTO_REF)) {
      routing.push(optionRow({ ref: AUTO_REF, label: AUTO_LABEL, reviewOk: true, floor: false, current }));
    }
    pushSection(sections, SECTION_SESSION, routing);

    const tiers = [];
    for (const [tier, info] of Object.entries(d.tiers ?? {})) {
      const ref = info?.ref;
      if (!ref || !matches(query, tier, ref)) continue;
      tiers.push(optionRow({
        ref,
        label: `${tier} · ${ref}`,
        reviewOk: true,
        floor: false,
        current,
      }));
    }
    pushSection(sections, SECTION_TIERS, tiers);
  }

  const enabled = [];
  for (const ref of d.enabled ?? []) {
    if (!ref || ref === "workbench/auto" || !matches(query, ref)) continue;
    enabled.push(optionRow({
      ref,
      label: ref,
      reviewOk: reviewOkLookup(d, ref),
      floor,
      current,
    }));
  }
  pushSection(sections, SECTION_ENABLED, enabled);

  for (const provider of d.catalogue?.providers ?? []) {
    const name = provider?.provider;
    if (!name) continue;
    const rows = [];
    for (const model of provider.models ?? []) {
      const ref = model?.ref;
      if (!ref) continue;
      const label = model.id ?? ref;
      if (!matches(query, label, ref, name, model.id)) continue;
      rows.push(optionRow({
        ref,
        label,
        reviewOk: model.review_ok !== false,
        floor,
        current,
      }));
    }
    pushSection(sections, name, rows);
  }

  const catalogueError = d.catalogue?.ok === false
    ? (d.catalogue.error || "Could not list models")
    : null;
  return { ok: true, catalogueError, sections };
}

export function isCustomValue(choicesDoc, target, current) {
  const cur = String(current ?? "").trim();
  if (!cur) return false;
  const opts = targetOptions(choicesDoc, target, "", cur);
  if (!opts.ok) return false;
  return !opts.sections.some((s) => s.rows.some((r) => r.ref === cur));
}

function piDefaultText(pi) {
  const base = pi.defaultProvider ? `${pi.defaultProvider}/${pi.defaultModel}` : String(pi.defaultModel);
  return pi.defaultThinkingLevel ? `${base}:${pi.defaultThinkingLevel}` : base;
}

export function previewModel(doc) {
  const rows = [];
  const claude = doc?.claude;
  const pi = doc?.pi;
  if (claude?.model) {
    const val = claude.effortLevel ? `${claude.model} (${claude.effortLevel})` : String(claude.model);
    rows.push({ key: "Claude model", value: val, mono: true });
  }
  for (const [key, val] of Object.entries(claude?.env ?? {})) {
    if (val) rows.push({ key, value: String(val), mono: true });
  }
  if (pi?.defaultModel) {
    rows.push({ key: "Pi default", value: piDefaultText(pi), mono: true });
  }
  if (Array.isArray(pi?.enabledModels) && pi.enabledModels.length > 0) {
    rows.push({ key: "Pi enabled", value: pi.enabledModels.join(", "), mono: true, wrap: true });
  }
  const skipped = Array.isArray(claude?.skipped) ? claude.skipped.filter(Boolean) : [];
  return { rows, skipped };
}

export function syncBarModel({ dirty = false, saving = false, syncing = false } = {}) {
  if (syncing) return { disabled: true, reason: "Syncing", status: "running", text: "Syncing" };
  if (saving) return { disabled: true, reason: "Saving", status: "running", text: "Saving" };
  if (!dirty) return { disabled: true, reason: CLEAN_REASON, status: null, text: CLEAN_REASON };
  return { disabled: false, reason: null, status: null, text: SYNC_CAPTION };
}

export async function loadConfigModels(exec, ctx = { on: null, cwd: null }) {
  const ran = await invoke(exec, ctx, "otto-workbench", configModelsArgv());
  if (!ran.ok) {
    return ran.error?.kind === "missing-tool"
      ? { ok: false, error: { kind: "outdated", message: OUTDATED_TEXT } }
      : ran;
  }
  const parsed = readJson(ran.stdout);
  if (!parsed.ok) return parsed;
  return parseConfigModels(parsed.value);
}

export async function loadModelChoices(exec, ctx = { on: null, cwd: null }) {
  const ran = await invoke(exec, ctx, "pr", ["models", "--json"], { timeout: CHOICES_TIMEOUT_MS });
  if (!ran.ok) return { ok: false, error: ran.error };
  const parsed = readJson(ran.stdout);
  if (!parsed.ok) return { ok: false, error: parsed.error };
  return parseChoices(parsed.value);
}

export async function saveTarget(exec, target, value, ctx = { on: null, cwd: null }) {
  const argv = writeTargetArgv(target, value);
  if (!argv) return { ok: false, error: { kind: "usage", message: "Choose or enter a model first" } };
  const ran = await invoke(exec, ctx, "otto-workbench", argv);
  return ran.ok ? { ok: true } : { ok: false, error: ran.error };
}

export async function runAiSync(exec, ctx = { on: null, cwd: null }) {
  const ran = await invoke(exec, ctx, "otto-workbench", syncArgv(), { timeout: SYNC_TIMEOUT_MS });
  return ran.ok ? { ok: true } : { ok: false, error: ran.error };
}
