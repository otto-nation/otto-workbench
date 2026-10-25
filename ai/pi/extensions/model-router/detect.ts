import { execFileSync } from "node:child_process";
import { realpathSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gitRootFor, nearestExistingDir, workspaceFor } from "../_shared/workspace.ts";
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

let cached: Tiers | undefined;

/**
 * The machine-wide tiers, asked of `config models` once per process.
 *
 * ceiling: read once, so a tier changed in config.yml reaches a running session
 * only after /reload or a new session. Upgrade to re-reading on the config
 * file's mtime if mid-session tier changes become something operators do.
 */
export function loadTiers(): Tiers {
  if (cached === undefined) {
    cached = parseTiers(
      execFileSync("python3", [CONFIG_CLI, "models"], {
        encoding: "utf8",
        stdio: ["ignore", "pipe", "pipe"],
      }),
    );
  }
  return cached;
}

/**
 * A predicate telling a plan or spec under CWD's workspace from any other file.
 *
 * The workspace is resolved once, when the predicate is built: it is a property
 * of the repository, not of the file. With no workspace (an ordinary clone, or
 * no repository at all) nothing is a plan, so the first edit moves the session
 * to building — the conservative answer, since building is the cheaper tier.
 */
export function planFileTest(cwd: string): (path: string) => boolean {
  // gitRootFor asks about the directory holding its argument, so name a file in
  // CWD rather than CWD itself, whose parent may be a bare container.
  const workspace = workspaceFor(gitRootFor(resolve(cwd, ".model-router-probe")));
  if (!workspace) return () => false;
  // Both spellings of each root: macOS reports /var as /private/var to anything
  // that canonicalises, and a tool path may arrive either way.
  const roots = ["plans", "specs"].flatMap((dir) => {
    const root = resolve(workspace, dir);
    return [root, canonical(root)];
  });
  return (path: string) => {
    const full = isAbsolute(path) ? path : resolve(cwd, path);
    return [full, canonical(full)].some((candidate) =>
      roots.some((root) => candidate === root || candidate.startsWith(root + "/")),
    );
  };
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
