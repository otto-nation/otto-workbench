# Pi extensions

Every subdirectory here except a `_`-prefixed one is installed into
`~/.pi/agent/extensions/` by `otto-workbench ai sync`, and removed from there
when it leaves this tree.
Pi discovers that directory on its own and follows symlinks into it, so an
extension is symlinked rather than copied and loads straight from the checkout.

An extension is a directory holding an `index.ts`:

```
ai/pi/extensions/<name>/index.ts
```

`index.js` and a `package.json` naming its own entry points also work — that is
Pi's rule, not the workbench's. TypeScript needs no build step; Pi loads it
through jiti. A directory with none of the three is skipped with a warning,
because an extension that cannot load is nearly always a mistake worth naming.

```ts
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  pi.on("session_start", async (event, ctx) => { /* … */ });
}
```

Do not start background resources from the factory — defer to `session_start`
and register an idempotent `session_shutdown` handler, or a `/reload` leaks one
per reload.

## What does not go here

An extension only some runs should load belongs in `../extensions-cli/`, which
this step does not install. Anything in *this* directory loads in **every** Pi
session on the machine, including ones with no repository and no environment set
up for it — `review-guard.ts` lives next door for exactly that reason.

Code shared *between* the extensions here goes in a `_`-prefixed directory, as
`_shared/` does. Those are skipped by name and never installed, so the missing
entry point is not reported as a fault every sync — and a shared module that
later grows a `package.json` for its own dependencies still does not start
loading as an extension. The extensions import it by relative path
(`../_shared/statements.ts`) — but only from a module their entry point imports,
never from `index.ts` itself. Pi loads the entry point by its installed symlink
path, so a `../_shared` import written in `index.ts` resolves beside the symlink
in `~/.pi/agent/extensions/`, where nothing is installed, and the extension fails
to load in every new session. A module `index.ts` imports is loaded from its real
path in the checkout, where `../_shared` resolves. So shared code reaches an
entry point through its sibling `detect.ts`, which re-exports what `index.ts`
needs, and only the extension directories have to reach
`~/.pi/agent/extensions/`. `tests/pi_extensions_install.bats` fails on an entry
point that imports a shared module directly.

`wait-guidance.ts` is the refusal alternative sleep-guard and background-guard
share: `job_start` when `getActiveTools()` lists it, otherwise a foreground bash
call bounded by the tool's timeout, so a session without that tool is not told
to use one.

## Overriding one

`~/.config/workbench/overrides/ai/pi/extensions/<name>/` replaces the extension
of that name, and `<name>.disabled` beside it suppresses the shipped one. Same
layering as `ai/skills`.
