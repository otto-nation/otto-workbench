/**
 * Per-physical-model spend for a Pi session: Auto's footer and session-brief.
 *
 * Imports nothing, so a test loads it under plain `node`. Cost is what Pi
 * already stored on the entry (`usage.cost`); this does not look up rates.
 */

export interface SpendItem {
  label: string;
  cost: number;
}

export function formatCost(cost: number): string {
  if (!(cost > 0)) return "$0.00";
  if (cost < 0.001) return "<$0.001";
  if (cost < 0.01) return `$${cost.toFixed(3)}`;
  return `$${cost.toFixed(2)}`;
}

export function shortModelLabel(name: string): string {
  const lower = name.toLowerCase();
  if (lower.includes("carbon")) return "carbon";
  if (lower.includes("opus")) return "opus";
  if (lower.includes("sonnet")) return "sonnet";
  if (lower.includes("haiku")) return "haiku";
  if (lower.includes("grok")) return "grok";
  if (lower.includes("gemini")) return "gemini";
  const slash = name.split("/")[0] ?? name;
  return slash.split(/[-/]/)[0] || name;
}

export function formatSpend(items: readonly SpendItem[]): string {
  return items
    .filter((item) => item.cost > 0)
    .map((item) => `${item.label} ${formatCost(item.cost)}`)
    .join(" · ");
}

/** Newest model id that produced an entry, or undefined. */
export function lastPhysicalId(entries: readonly unknown[]): string | undefined {
  for (let i = entries.length - 1; i >= 0; i--) {
    const id = modelOf(entries[i]);
    if (id !== "unknown") return id;
  }
  return undefined;
}

export function footerLines(input: { physicalId?: string; spend: readonly SpendItem[] }): [string, string] {
  const who = input.physicalId ? `auto → ${input.physicalId}` : "auto";
  return [who, formatSpend(input.spend) || "$0.00"];
}

function costOf(usage: unknown): number {
  if (usage == null || typeof usage !== "object") return 0;
  const cost = (usage as { cost?: unknown }).cost;
  if (typeof cost === "number") return cost;
  if (cost != null && typeof cost === "object") {
    const total = (cost as { total?: unknown }).total;
    if (typeof total === "number") return total;
  }
  return 0;
}

function usageOf(entry: unknown): unknown {
  if (entry == null || typeof entry !== "object") return undefined;
  const rec = entry as { usage?: unknown; message?: { usage?: unknown } };
  return rec.message?.usage ?? rec.usage;
}

function modelOf(entry: unknown): string {
  if (entry == null || typeof entry !== "object") return "unknown";
  const rec = entry as {
    modelId?: unknown;
    message?: { responseModel?: unknown; model?: unknown };
  };
  for (const value of [rec.message?.responseModel, rec.message?.model, rec.modelId]) {
    if (typeof value === "string" && value) return value;
  }
  return "unknown";
}

/** Sum `usage.cost` by short model label; drop zeros; highest first. */
export function spendFromEntries(entries: readonly unknown[]): SpendItem[] {
  const totals = new Map<string, number>();
  for (const entry of entries) {
    const cost = costOf(usageOf(entry));
    if (!(cost > 0)) continue;
    const label = shortModelLabel(modelOf(entry));
    totals.set(label, (totals.get(label) ?? 0) + cost);
  }
  return [...totals.entries()]
    .map(([label, cost]) => ({ label, cost }))
    .sort((a, b) => b.cost - a.cost);
}
