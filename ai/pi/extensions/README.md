# Pi extensions

Every subdirectory here is installed into `~/.pi/agent/extensions/` by
`otto-workbench ai sync`, and removed from there when it leaves this tree.
Pi discovers that directory on its own and follows symlinks into it, so an
extension is symlinked rather than copied and loads straight from the checkout.

An extension is a directory holding an `index.ts`:

```
ai/pi/extensions/<name>/index.ts
```

`index.js` and a `package.json` naming its own entry points also work — that is
Pi's rule, not the workbench's. TypeScript needs no build step; Pi loads it
through jiti. A directory with none of the three is skipped with a warning.

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

## Overriding one

`~/.config/workbench/overrides/ai/pi/extensions/<name>/` replaces the extension
of that name, and `<name>.disabled` beside it suppresses the shipped one. Same
layering as `ai/skills`.
