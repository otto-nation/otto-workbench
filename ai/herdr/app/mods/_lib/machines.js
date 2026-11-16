// Names of the remote machines herdr can reach; the Queue and Hosts panels poll these.
import { spaces } from "herdr";
import * as herdr from "herdr";

function uniqueOns(values) {
  const seen = new Set();
  const out = [];
  for (const on of values) {
    if (on == null || on === "") continue;
    const key = String(on);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(key);
  }
  out.sort();
  return out;
}

function fromSpaces() {
  return uniqueOns((spaces() ?? []).map((s) => s?.machine));
}

export function remoteOns() {
  if (typeof herdr.machines !== "function") return fromSpaces();
  const list = herdr.machines();
  if (!Array.isArray(list)) return fromSpaces();
  return uniqueOns(list
    .filter((m) => m != null && m.name != null && m.layout !== "unavailable")
    .map((m) => m.name));
}
