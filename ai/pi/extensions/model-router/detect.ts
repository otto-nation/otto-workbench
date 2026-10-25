import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { nearestExistingDir, workspaceFor } from "../_shared/workspace.ts";
import type { Tier } from "./route.ts";

export * from "./route.ts";

/**
 * What the router reads from outside Pi: the tiers, and where plans live.
 *
 * Node builtins only, so a test loads it under `node`. index.ts imports from
 * here rather than from `../_shared` itself, which would resolve beside its
 * installed symlink, where nothing is (see ../README.md).
 */

/** `lib/config_cli.py`, the one reader of config.yml, resolved from this file. */
const CONFIG_CLI = fileURLToPath(new URL("../../../../lib/config_cli.py", import.meta.url));

export interface TierRef {
  provider: string;
  id: string;
  level: string | null;
}

export type Tiers = Record<Tier, TierRef>;

const TIERS: readonly Tier[] = ["deep", "standard", "light"];

/**
 * The three tiers, parsed from `config models` output TEXT.
 *
 * Throws, naming the key to fix, for a tier that is unset or names no provider:
 * Pi ends a request whose route throws with that message, which is where the
 * operator is looking. Sync refuses the same config, so this is reached only
 * when config.yml changed after the last sync.
 */
export function parseTiers(text: string): Tiers {
  const tiers = (JSON.parse(text) as { tiers?: Partial<Record<Tier, TierRef>> }).tiers ?? {};
  const missing = TIERS.filter((tier) => !tiers[tier]?.provider);
  if (missing.length > 0) {
    throw new Error(
      `workbench/auto routes between all three tiers, and each must name its provider — ` +
        `set ${missing.map((tier) => `models.${tier}`).join(", ")} in config.yml (e.g. anthropic/claude-opus-5-5)`,
    );
  }
  return tiers as Tiers;
}

let cached: Tiers | Error | undefined;

/**
 * The machine-wide tiers, asked of `config models` once per process.
 *
 * A failure is cached as well as a success, so a broken config costs one
 * Python start rather than one per request, and is thrown as the line
 * `config models` printed rather than as node's wrapper around it.
 *
 * ceiling: read once, so a tier changed in config.yml reaches a running session
 * only after /reload or a new session. Upgrade to re-reading on the config
 * file's mtime if mid-session tier changes become something operators do.
 */
export function loadTiers(): Tiers {
  if (cached === undefined) {
    try {
      cached = parseTiers(
        execFileSync("python3", [CONFIG_CLI, "models"], {
          encoding: "utf8",
          stdio: ["ignore", "pipe", "pipe"],
          env: { ...process.env, NO_COLOR: "1" },
        }),
      );
    } catch (error) {
      cached = new Error(`workbench/auto could not read the tiers: ${failureLine(error)}`);
    }
  }
  if (cached instanceof Error) throw cached;
  return cached;
}

/** What a failed `config models` said, without colour or node's command echo. */
function failureLine(error: unknown): string {
  const stderr = String((error as { stderr?: unknown }).stderr ?? "");
  const said = stderr.replace(/\x1b\[[0-9;]*m/g, "").split("\n").map((line) => line.trim()).filter(Boolean);
  return said.at(-1)?.replace(/^✗\s*/, "") ?? (error instanceof Error ? error.message : String(error));
}

/**
 * A predicate telling a plan or spec from any other file, for tool paths
 * relative to CWD.
 *
 * Each path is asked about its own workspace, the way workspace-guard asks: a
 * session started from a bare-repo container has no work tree to resolve from,
 * and one planning for another repository writes into that repository's
 * workspace. `resolve-workspace` answers for any path under a container, its
 * workspace included. A path with no workspace (an ordinary clone, or no
 * repository) is not a plan. Verdicts are memoised per path, since the router
 * asks on every request and each answer spawns `resolve-workspace`.
 */
export function planFileTest(cwd: string): (path: string) => boolean {
  const verdicts = new Map<string, boolean>();
  return (path: string) => {
    const full = isAbsolute(path) ? path : resolve(cwd, path);
    let verdict = verdicts.get(full);
    if (verdict === undefined) {
      verdict = isPlanFile(full);
      verdicts.set(full, verdict);
    }
    return verdict;
  };
}

function isPlanFile(full: string): boolean {
  const workspace = workspaceFor(nearestExistingDir(full));
  if (!workspace) return false;
  // Both spellings of each side: macOS reports /var as /private/var to anything
  // that canonicalises, and a tool path may arrive either way.
  const roots = ["plans", "specs"].flatMap((dir) => {
    const root = resolve(workspace, dir);
    return [root, canonical(root)];
  });
  return [full, canonical(full)].some((candidate) =>
    roots.some((root) => candidate === root || candidate.startsWith(root + "/")),
  );
}

/** PATH with symlinks resolved through its nearest existing ancestor. */
function canonical(path: string): string {
  const base = nearestExistingDir(path + "/.");
  try {
    return resolve(realpathSync(base), relative(base, path));
  } catch {
    return path;
  }
}
