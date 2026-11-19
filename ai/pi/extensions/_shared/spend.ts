/**
 * Per-physical-model spend for a Pi session: Auto's status line and session-brief.
 *
 * Imports nothing, so a test loads it under plain `node`. Cost is what Pi
 * already stored on the entry (`usage.cost`) or on a tool result's
 * `details.results` (subagent). This does not look up rates.
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
  const model = name.split("/").pop() ?? name;
  return model.split("-")[0] || name;
}

export function formatSpend(items: readonly SpendItem[]): string {
  return items
    .filter((item) => item.cost > 0)
    .map((item) => `${item.label} ${formatCost(item.cost)}`)
    .join(" · ");
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

function modelOf(entry: unknown, includeTopLevel = false): string {
  if (entry == null || typeof entry !== "object") return "unknown";
  const rec = entry as {
    model?: unknown;
    modelId?: unknown;
    message?: { responseModel?: unknown; model?: unknown };
  };
  for (const value of [rec.message?.responseModel, rec.message?.model, rec.modelId, includeTopLevel ? rec.model : undefined]) {
    if (typeof value === "string" && value) return value;
  }
  return "unknown";
}

function resultsOf(entry: unknown): unknown[] | undefined {
  if (entry == null || typeof entry !== "object") return undefined;
  const rec = entry as { details?: unknown; message?: { details?: unknown } };
  const details = rec.message?.details ?? rec.details;
  if (details == null || typeof details !== "object") return undefined;
  const recDetails = details as { mode?: unknown; results?: unknown };
  if (recDetails.mode !== "single" && recDetails.mode !== "parallel" && recDetails.mode !== "chain") {
    return undefined;
  }
  const results = recDetails.results;
  if (!Array.isArray(results) || results.length === 0) return undefined;
  return results;
}

function addCost(totals: Map<string, number>, item: unknown): void {
  const cost = costOf(usageOf(item));
  if (!(cost > 0)) return;
  const label = shortModelLabel(modelOf(item, true));
  totals.set(label, (totals.get(label) ?? 0) + cost);
}

/** Sum `usage.cost` by short model label; drop zeros; highest first. */
export function spendFromEntries(entries: readonly unknown[]): SpendItem[] {
  const totals = new Map<string, number>();
  for (const entry of entries) {
    const nested = resultsOf(entry);
    if (nested) {
      for (const result of nested) addCost(totals, result);
      continue;
    }
    addCost(totals, entry);
  }
  return [...totals.entries()]
    .map(([label, cost]) => ({ label, cost }))
    .sort((a, b) => b.cost - a.cost);
}
