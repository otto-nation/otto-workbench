# Changelog

## [1.40.3](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.40.2...otto-ai-tools-v1.40.3) (2026-07-24)


### Bug Fixes

* **pr-rebase:** auto-resolve generated files instead of AI resolution ([#547](https://github.com/otto-nation/otto-workbench/issues/547)) ([cc5da99](https://github.com/otto-nation/otto-workbench/commit/cc5da99398d40ba70b221a39e9a117764d26c287))

## [1.40.2](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.40.1...otto-ai-tools-v1.40.2) (2026-07-24)


### Bug Fixes

* **pr-comments:** add permalinks for comment items and reviewer column in summary ([#544](https://github.com/otto-nation/otto-workbench/issues/544)) ([f0d98f5](https://github.com/otto-nation/otto-workbench/commit/f0d98f5973d95b8927e226d669d06080d8017e7b))

## [1.40.1](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.40.0...otto-ai-tools-v1.40.1) (2026-07-24)


### Bug Fixes

* **review:** retry synthesis on transient API errors; detect self-review fallback ([#541](https://github.com/otto-nation/otto-workbench/issues/541)) ([89b02d9](https://github.com/otto-nation/otto-workbench/commit/89b02d92f79f5a227d8128096a5cd372c7fa3bbb))

## [1.40.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.39.2...otto-ai-tools-v1.40.0) (2026-07-24)


### Features

* **ci-check:** improve extraction robustness and artifact fallback ([#539](https://github.com/otto-nation/otto-workbench/issues/539)) ([55f93a8](https://github.com/otto-nation/otto-workbench/commit/55f93a83792b9d6b339490d8e18e4b91673d1e77))
* **ci-check:** rebase onto main before fixing CI failures ([#526](https://github.com/otto-nation/otto-workbench/issues/526)) ([1a74710](https://github.com/otto-nation/otto-workbench/commit/1a747104550c3188de022e97a7c89e42d7fd1223))
* **pr-comments:** decompose top-level comments into trackable items ([#528](https://github.com/otto-nation/otto-workbench/issues/528)) ([9036ae1](https://github.com/otto-nation/otto-workbench/commit/9036ae19e3aba05c699a6890e5b9cb6d3710a8d5))


### Bug Fixes

* **ci-check:** rebase after capturing failure data, not before ([#536](https://github.com/otto-nation/otto-workbench/issues/536)) ([9e8d614](https://github.com/otto-nation/otto-workbench/commit/9e8d61430c6124d470fbe6ce218e3bc08cf1cd88))
* **ci-check:** report in-progress runs instead of false success ([#531](https://github.com/otto-nation/otto-workbench/issues/531)) ([9da1d6a](https://github.com/otto-nation/otto-workbench/commit/9da1d6a203e79ac4c3b3f7ea67d9df51d96bd367))
* **pr-comments:** include issue link in deferred summary rows ([#534](https://github.com/otto-nation/otto-workbench/issues/534)) ([2aa10ff](https://github.com/otto-nation/otto-workbench/commit/2aa10ffd06956632a9ae45e69743a02fa807bdec))
* **pr-comments:** remove false-positive reconciliation; defer replies until --resolve ([#523](https://github.com/otto-nation/otto-workbench/issues/523)) ([a94bec5](https://github.com/otto-nation/otto-workbench/commit/a94bec5e574fc77128d07d8c450052320e4e87d4))
* **pr-comments:** remove file-level reconciliation that falsely resolves threads ([#540](https://github.com/otto-nation/otto-workbench/issues/540)) ([55993a6](https://github.com/otto-nation/otto-workbench/commit/55993a6060d3e3e42f000c41ab48a9708095799c))
* **pr:** prefer resolved PR number over --branch in delegate dispatch ([#538](https://github.com/otto-nation/otto-workbench/issues/538)) ([2d827f1](https://github.com/otto-nation/otto-workbench/commit/2d827f1fcf81d296848ec8f933104f438c69ee2a))
* **retro-scan:** use Path.home() for robust home resolution ([#530](https://github.com/otto-nation/otto-workbench/issues/530)) ([2b1a3c6](https://github.com/otto-nation/otto-workbench/commit/2b1a3c6660c74d4ce5102d3013b81dd111e53286))
* **retro:** distinguish global vs project rules in placement ([#529](https://github.com/otto-nation/otto-workbench/issues/529)) ([c31e305](https://github.com/otto-nation/otto-workbench/commit/c31e30504dd82018d45ff455b8b2e9ec470aa58b))
* **review-post:** re-verify inline positions on SHA drift instead of falling back to comment ([#527](https://github.com/otto-nation/otto-workbench/issues/527)) ([7f8479a](https://github.com/otto-nation/otto-workbench/commit/7f8479a592873c8186ee748a5d6b779f196fb75f))
* **settings:** add bare Read permission to auto-allow list ([#535](https://github.com/otto-nation/otto-workbench/issues/535)) ([57a164b](https://github.com/otto-nation/otto-workbench/commit/57a164bf76b64c9ee15f81bee91a0bf9dc539034))


### Code Refactoring

* **pr-comments:** consolidate thread model types ([#537](https://github.com/otto-nation/otto-workbench/issues/537)) ([48c4363](https://github.com/otto-nation/otto-workbench/commit/48c43632150eb743bd816870a60d2a6325f23757))

## [1.39.2](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.39.1...otto-ai-tools-v1.39.2) (2026-07-21)


### Bug Fixes

* **review:** strip bold-wrapped verdict action prefix before posting ([#520](https://github.com/otto-nation/otto-workbench/issues/520)) ([58cdd25](https://github.com/otto-nation/otto-workbench/commit/58cdd2556bd7223ab365fbeda996451defc883d5))

## [1.39.1](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.39.0...otto-ai-tools-v1.39.1) (2026-07-21)


### Bug Fixes

* **pr-comments:** post replies for already-addressed threads ([#519](https://github.com/otto-nation/otto-workbench/issues/519)) ([a934e04](https://github.com/otto-nation/otto-workbench/commit/a934e043b037192a91a3bbcdafa7a0801775292f))
* **review:** default _confirm to False when stdin is not interactive ([#516](https://github.com/otto-nation/otto-workbench/issues/516)) ([f6e1cdc](https://github.com/otto-nation/otto-workbench/commit/f6e1cdc19123760fd19d9a72377b35954453c70d))

## [1.39.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.38.0...otto-ai-tools-v1.39.0) (2026-07-20)


### Features

* **ci-check:** enrich failure context; parallelize API calls; remove confirmation gates ([#504](https://github.com/otto-nation/otto-workbench/issues/504)) ([1f7ffde](https://github.com/otto-nation/otto-workbench/commit/1f7ffdef2444e34ff65a7ed91d2a75331dc2c0f9))
* **ci-failures:** auto-fix without confirmation and enrich BUILD failure context ([#501](https://github.com/otto-nation/otto-workbench/issues/501)) ([f4fc928](https://github.com/otto-nation/otto-workbench/commit/f4fc928fbe84530df0a8a82d72b33bb2e63e3ed9))
* **pr-comments:** give fix agent access to main worktree for baseline context ([#499](https://github.com/otto-nation/otto-workbench/issues/499)) ([1cacbc5](https://github.com/otto-nation/otto-workbench/commit/1cacbc53b5957098d78fb86c5730c4c5ae4c4bd4))
* **reviewer:** add re-review verification with thread-based resolution ([#502](https://github.com/otto-nation/otto-workbench/issues/502)) ([95d4958](https://github.com/otto-nation/otto-workbench/commit/95d495899e3a3394ae34c581ed2c4105f629b395))


### Bug Fixes

* **ai:** handle BrokenPipeError in subprocess stdin write ([#511](https://github.com/otto-nation/otto-workbench/issues/511)) ([a229135](https://github.com/otto-nation/otto-workbench/commit/a229135e81d905fadc83305be0afc32aa39c7e2a))
* **pr-context:** use fuzzy resolution for bare-repo worktree lookup ([#503](https://github.com/otto-nation/otto-workbench/issues/503)) ([0a9b57c](https://github.com/otto-nation/otto-workbench/commit/0a9b57c8180128dddd3ba3636872579d62d8580b))
* **pr-rebase:** handle modify/delete conflicts without AI ([#509](https://github.com/otto-nation/otto-workbench/issues/509)) ([be1b833](https://github.com/otto-nation/otto-workbench/commit/be1b8335b68805e4325a29a684919f9ba5ff9110))
* **pr-rebase:** resolve branch during rebase; surface AI prompt errors ([#506](https://github.com/otto-nation/otto-workbench/issues/506)) ([63b5f9d](https://github.com/otto-nation/otto-workbench/commit/63b5f9d8e6a55ab8be82cad50d10d25b939907bf))
* **pr:** remove consumed positional from extra; skip stash mid-rebase ([#505](https://github.com/otto-nation/otto-workbench/issues/505)) ([3f6ebca](https://github.com/otto-nation/otto-workbench/commit/3f6ebcaa4df2b20d46089e81dd92e508829d61e5))
* **review:** configurable diff floor; drop file contents on overflow ([#515](https://github.com/otto-nation/otto-workbench/issues/515)) ([25cf7a0](https://github.com/otto-nation/otto-workbench/commit/25cf7a0a2109dd60fb23a691f9412f22be1d8c6b))
* **review:** stop pruning merged reviews on every run ([#507](https://github.com/otto-nation/otto-workbench/issues/507)) ([d05cb4a](https://github.com/otto-nation/otto-workbench/commit/d05cb4a1219c8768822421fedac17dc94e80d281))
* **review:** strip unfenced blockquote evidence from review output ([#508](https://github.com/otto-nation/otto-workbench/issues/508)) ([eb7e201](https://github.com/otto-nation/otto-workbench/commit/eb7e201d7b867f7c94ee119981855b0221b4becd))
* **review:** strip verdict action prefix from posted review body ([#510](https://github.com/otto-nation/otto-workbench/issues/510)) ([0cbfb97](https://github.com/otto-nation/otto-workbench/commit/0cbfb97475549db4056157d68100c8924ada0b9e))
* **settings:** add permission for skill scripts; document $VAR expansion ([#514](https://github.com/otto-nation/otto-workbench/issues/514)) ([8f9ada7](https://github.com/otto-nation/otto-workbench/commit/8f9ada760ae0875d96f3eb80e6ada60473c489ea))

## [1.38.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.37.0...otto-ai-tools-v1.38.0) (2026-07-16)


### Features

* **review-post:** add summary/verdict to body and improve nit formatting ([#496](https://github.com/otto-nation/otto-workbench/issues/496)) ([d5dfb1a](https://github.com/otto-nation/otto-workbench/commit/d5dfb1afa37994ba814285b0cb0ddcd5f6c10bc7))


### Bug Fixes

* **pr-comments:** handle AI preamble text before JSON in triage output ([#494](https://github.com/otto-nation/otto-workbench/issues/494)) ([687ab02](https://github.com/otto-nation/otto-workbench/commit/687ab02de9f003c290a42fb46d5c486974a5f2fa))

## [1.37.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.36.0...otto-ai-tools-v1.37.0) (2026-07-15)


### Features

* **ci-check:** extract failed step name, add drift log markers ([#491](https://github.com/otto-nation/otto-workbench/issues/491)) ([4747fb7](https://github.com/otto-nation/otto-workbench/commit/4747fb791d80ac16f1308388c5c633eb262cdc31))
* **pr-comments:** deferred thread tracking, issue lifecycle, and thread resolution ([#488](https://github.com/otto-nation/otto-workbench/issues/488)) ([c0fc5b8](https://github.com/otto-nation/otto-workbench/commit/c0fc5b81dbd0e9b14729f2224b3c00c8c069cd50))
* **review:** integrate PR state and role awareness into review prompts ([#489](https://github.com/otto-nation/otto-workbench/issues/489)) ([6d0dfc2](https://github.com/otto-nation/otto-workbench/commit/6d0dfc2d5137e00c5c4d7955f30a4fc72850dfbf))


### Bug Fixes

* **pr-comments:** recover agent commit SHA when script commit fails ([#486](https://github.com/otto-nation/otto-workbench/issues/486)) ([fdf2c33](https://github.com/otto-nation/otto-workbench/commit/fdf2c3388cd0d529088bf02b0ce7538295653b9f))
* **review:** skip incremental delta when prior SHA equals HEAD ([#487](https://github.com/otto-nation/otto-workbench/issues/487)) ([517a3d5](https://github.com/otto-nation/otto-workbench/commit/517a3d591001c491e43ab570e0c2dbb8c5fceb91))
* **review:** stop printing JSON summary to stdout, suppress false incomplete warnings ([#483](https://github.com/otto-nation/otto-workbench/issues/483)) ([d9edafb](https://github.com/otto-nation/otto-workbench/commit/d9edafb06b903e2cc7d511b0c4956fe005b23017))

## [1.36.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.35.1...otto-ai-tools-v1.36.0) (2026-07-14)


### Features

* **review:** show findings, verdict, and phase warnings in summary ([#481](https://github.com/otto-nation/otto-workbench/issues/481)) ([26c68f2](https://github.com/otto-nation/otto-workbench/commit/26c68f239723527ecf545782351bae32b80a69da))


### Bug Fixes

* **pr-context:** skip update_to_remote when worktree is on a different branch ([#475](https://github.com/otto-nation/otto-workbench/issues/475)) ([5db16f5](https://github.com/otto-nation/otto-workbench/commit/5db16f583b40569b2ce9ac02554af01a5f98a467))
* **pr:** handle SIGINT to prevent traceback on Ctrl+C ([#478](https://github.com/otto-nation/otto-workbench/issues/478)) ([a87522b](https://github.com/otto-nation/otto-workbench/commit/a87522b6ce51c974c08046321f2352a4d5ede7fc))
* **review:** enable disprove phase at medium effort ([#479](https://github.com/otto-nation/otto-workbench/issues/479)) ([4ad9dc8](https://github.com/otto-nation/otto-workbench/commit/4ad9dc892871820341f9e2a973c73d247ce5fc58))
* **review:** log issue detection attempts before prompting ([#480](https://github.com/otto-nation/otto-workbench/issues/480)) ([8ee3401](https://github.com/otto-nation/otto-workbench/commit/8ee3401a1815d98d94c97c8f922f5f9423a6223e))
* **review:** mark review as error when group agents fail ([#482](https://github.com/otto-nation/otto-workbench/issues/482)) ([c1cf672](https://github.com/otto-nation/otto-workbench/commit/c1cf672d9a0e5a3edb4827a9f19ce126383281b3))

## [1.35.1](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.35.0...otto-ai-tools-v1.35.1) (2026-07-13)


### Bug Fixes

* **comments:** defer fix summary comment until discussion is complete ([#470](https://github.com/otto-nation/otto-workbench/issues/470)) ([64330e6](https://github.com/otto-nation/otto-workbench/commit/64330e674e06ab573d2248c955e7c68688212417))
* **comments:** surface review-level body comments in pr-comments ([#472](https://github.com/otto-nation/otto-workbench/issues/472)) ([41b471b](https://github.com/otto-nation/otto-workbench/commit/41b471bc20c500ff51cf400f9149b4ee041b6164))
* **rebase:** include base-side context for AI conflict resolution ([#471](https://github.com/otto-nation/otto-workbench/issues/471)) ([121cfd7](https://github.com/otto-nation/otto-workbench/commit/121cfd72d2d3d561b96ab264a76ebafcecd1ab1d))
* **review-threads:** commit regenerated files and surface non-inline comments ([#473](https://github.com/otto-nation/otto-workbench/issues/473)) ([0260dd7](https://github.com/otto-nation/otto-workbench/commit/0260dd75ade1ad3432b0afe3aebe59af500cc040))
* **review:** classify positional targets as PR or branch before resolving context ([#466](https://github.com/otto-nation/otto-workbench/issues/466)) ([060e2a3](https://github.com/otto-nation/otto-workbench/commit/060e2a3e2134548f14b5d50add7fb9765aedd842))

## [1.35.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.34.0...otto-ai-tools-v1.35.0) (2026-07-10)


### Features

* **review:** separate cache tokens from fresh in usage summary ([#464](https://github.com/otto-nation/otto-workbench/issues/464)) ([9d5f08e](https://github.com/otto-nation/otto-workbench/commit/9d5f08ef018f29d6796e35620e73e3d70e5e1392))
* **review:** set review status to error when synthesis agent fails ([#459](https://github.com/otto-nation/otto-workbench/issues/459)) ([15e1b49](https://github.com/otto-nation/otto-workbench/commit/15e1b491dd992bcb16417b40cfdb5d9ba806c36e))


### Bug Fixes

* **comments:** post dismissal replies for invalid suggestion threads ([#465](https://github.com/otto-nation/otto-workbench/issues/465)) ([c13e33e](https://github.com/otto-nation/otto-workbench/commit/c13e33e3fbc56d1dca480892d4121cba98e5ed2b))
* **review:** inject custom agent definitions in --bare mode ([#462](https://github.com/otto-nation/otto-workbench/issues/462)) ([b0e003f](https://github.com/otto-nation/otto-workbench/commit/b0e003f79ac2fd17ecb4963752c4f7df93ab0c10))
* **review:** stop auto-injecting --self for branch positionals and add review discovery fallback ([#463](https://github.com/otto-nation/otto-workbench/issues/463)) ([1d14547](https://github.com/otto-nation/otto-workbench/commit/1d145479e5918b10a6d842b7e96813dae4fea740))

## [1.34.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.33.0...otto-ai-tools-v1.34.0) (2026-07-09)


### Features

* **pr:** fetch and reset worktree to remote before pr commands ([#456](https://github.com/otto-nation/otto-workbench/issues/456)) ([5beede8](https://github.com/otto-nation/otto-workbench/commit/5beede8b8327f7a399dfd71f335b3b5f5e505060))
* **review:** add lead scout, disprove gate, and review profiles ([#458](https://github.com/otto-nation/otto-workbench/issues/458)) ([ffbe6d2](https://github.com/otto-nation/otto-workbench/commit/ffbe6d238c5ba49bd53e03ac86685b4aa741face))


### Bug Fixes

* **pr-comments:** track seen issue-level discussion comments in state ([#453](https://github.com/otto-nation/otto-workbench/issues/453)) ([ef75eb5](https://github.com/otto-nation/otto-workbench/commit/ef75eb5403366510eb7f3f17cb0071a697ff1c6d))
* **pr:** prevent --self injection when PR target comes from global flag or context ([#457](https://github.com/otto-nation/otto-workbench/issues/457)) ([52f19c0](https://github.com/otto-nation/otto-workbench/commit/52f19c00eb9253a2cc355ce52ce91d07f40a2cf7))

## [1.33.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.32.0...otto-ai-tools-v1.33.0) (2026-07-02)


### Features

* **ai:** add --effort and --max-groups flags to claude-review ([#442](https://github.com/otto-nation/otto-workbench/issues/442)) ([313bf9a](https://github.com/otto-nation/otto-workbench/commit/313bf9a1c650b07b97ebd609c87a5b084aa4b2a6))
* **ai:** add retry hint when group agent hits max turns ([#450](https://github.com/otto-nation/otto-workbench/issues/450)) ([90ce0c3](https://github.com/otto-nation/otto-workbench/commit/90ce0c39f2e6bfdf7cedb4b1403e757c645fc39e))
* **ai:** add reviewer-lite agent for group/angles/fix phases ([#447](https://github.com/otto-nation/otto-workbench/issues/447)) ([5a6bfc6](https://github.com/otto-nation/otto-workbench/commit/5a6bfc6e143f4e96b7cb3278216ca056409a6eae))
* **ai:** drop prior review from synthesis prompts ([#444](https://github.com/otto-nation/otto-workbench/issues/444)) ([9c2a422](https://github.com/otto-nation/otto-workbench/commit/9c2a4224fbf44214d78e9dc26ecd0be9650ada3b))
* **ai:** extract project context from preflight into template variable ([#445](https://github.com/otto-nation/otto-workbench/issues/445)) ([3bdd879](https://github.com/otto-nation/otto-workbench/commit/3bdd8792847a44b44d831625c14d6ef793da6817))
* **ai:** prefer merging review groups with shared directory prefix ([#451](https://github.com/otto-nation/otto-workbench/issues/451)) ([8f1a502](https://github.com/otto-nation/otto-workbench/commit/8f1a50297ca4335e8f65422101bc2717a1cf5602))
* **ai:** reorder group template for prompt cache alignment ([#446](https://github.com/otto-nation/otto-workbench/issues/446)) ([736349b](https://github.com/otto-nation/otto-workbench/commit/736349b809f11a269a8a64451de89bda204c523e))
* **ai:** scope delta, reply threads, and PR header per group ([#443](https://github.com/otto-nation/otto-workbench/issues/443)) ([8fe5693](https://github.com/otto-nation/otto-workbench/commit/8fe56930f35e518daad587f712bad40cc4de5f1b))


### Bug Fixes

* **ai:** add early-exit to ci-failures skill for no-failure cases ([#438](https://github.com/otto-nation/otto-workbench/issues/438)) ([11273a3](https://github.com/otto-nation/otto-workbench/commit/11273a3bebe68a95e5e919e011b7bb008c011f99))
* **ai:** improve pr-rebase conflict resolution parse diagnostics ([#440](https://github.com/otto-nation/otto-workbench/issues/440)) ([61b6868](https://github.com/otto-nation/otto-workbench/commit/61b6868f25067a14cba08e19caaa9442e85ec2a8))
* **ai:** remove dead diff from review sidecar meta.json ([#448](https://github.com/otto-nation/otto-workbench/issues/448)) ([032a393](https://github.com/otto-nation/otto-workbench/commit/032a393531250a9b6ad8faa3ac3f46c1483404c4))


### Code Refactoring

* **ai:** parse delta file names from diff headers instead of separate git call ([#449](https://github.com/otto-nation/otto-workbench/issues/449)) ([a8020e9](https://github.com/otto-nation/otto-workbench/commit/a8020e9ca66d9e018f0c0dfa998cebf5c61631e8))
* **ai:** reduce post-processing file re-reads to single read/write ([#452](https://github.com/otto-nation/otto-workbench/issues/452)) ([9d05339](https://github.com/otto-nation/otto-workbench/commit/9d05339e564eab22d788766239321542c36c254f))

## [1.32.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.31.3...otto-ai-tools-v1.32.0) (2026-07-01)


### Features

* **ai:** add ceiling convention, debt tracking, and reuse hooks ([#427](https://github.com/otto-nation/otto-workbench/issues/427)) ([555aedd](https://github.com/otto-nation/otto-workbench/commit/555aedd42160101657d810fd6b1acba1a7dc77b5))
* **ai:** add statusline, reference card, and subagent reuse injection ([#435](https://github.com/otto-nation/otto-workbench/issues/435)) ([7caf27a](https://github.com/otto-nation/otto-workbench/commit/7caf27addb072dcf6db0878febc2437c91734385))
* **ai:** ceiling convention, reuse levels, subagent propagation ([#428](https://github.com/otto-nation/otto-workbench/issues/428)) ([8c09249](https://github.com/otto-nation/otto-workbench/commit/8c092493856afd9b60bdc2030e84d0f4f2eb185e))
* **brew:** replace headroom with rtk for token compression ([#417](https://github.com/otto-nation/otto-workbench/issues/417)) ([6355781](https://github.com/otto-nation/otto-workbench/commit/63557810a133b325ac05f62f0e5614b4d7e58efc))
* **ci-check:** add --fix flag for AI-driven CI failure fixes ([#414](https://github.com/otto-nation/otto-workbench/issues/414)) ([a713c82](https://github.com/otto-nation/otto-workbench/commit/a713c82ea62c2bd1de1e00232e3559560376b064))


### Bug Fixes

* **ai:** auto-commit regenerated files when pr rebase push fails ([#433](https://github.com/otto-nation/otto-workbench/issues/433)) ([35ac22e](https://github.com/otto-nation/otto-workbench/commit/35ac22ea1dd13c773d35907c7a5847c9fa873eb1))
* **ai:** improve review-threads error handling for commit/push failures ([#423](https://github.com/otto-nation/otto-workbench/issues/423)) ([0d93f3f](https://github.com/otto-nation/otto-workbench/commit/0d93f3f6e33f51ed12216866b9c7cd6d3257c293))
* **ai:** prevent pr-rebase from aborting when next commit has conflicts ([#432](https://github.com/otto-nation/otto-workbench/issues/432)) ([a510a4e](https://github.com/otto-nation/otto-workbench/commit/a510a4ee642e6f9f11caee0445e6e57a26e6d6fc))
* **ai:** skip non-failure jobs in ci-check ([#429](https://github.com/otto-nation/otto-workbench/issues/429)) ([a3ba315](https://github.com/otto-nation/otto-workbench/commit/a3ba31512caa4d03e931df7996ced9d01932e15f))
* **ai:** stop posting redundant summary; include issue comments in fix output ([#431](https://github.com/otto-nation/otto-workbench/issues/431)) ([24690a5](https://github.com/otto-nation/otto-workbench/commit/24690a5b431c5df15d04b33346d89b98da8e520b))
* **ai:** track source_run_id per failure in ci-check multi-run merging ([#434](https://github.com/otto-nation/otto-workbench/issues/434)) ([7e3435b](https://github.com/otto-nation/otto-workbench/commit/7e3435b2830eb6734e5f8ca94f08d602d5884cd1))


### Code Refactoring

* **ai:** rename context skill and file to architecture ([#420](https://github.com/otto-nation/otto-workbench/issues/420)) ([1908959](https://github.com/otto-nation/otto-workbench/commit/190895900a6618f87bad8582d3a44b9883b71084))

## [1.31.3](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.31.2...otto-ai-tools-v1.31.3) (2026-06-29)


### Bug Fixes

* **pr-rebase:** auto-stash dirty tree; stage all tidy changes; abort on continue failure ([#408](https://github.com/otto-nation/otto-workbench/issues/408)) ([15572c6](https://github.com/otto-nation/otto-workbench/commit/15572c6912103d7dccd69d53a14e25bad0b1ba4c))
* self-review findings ([#412](https://github.com/otto-nation/otto-workbench/issues/412)) ([0569472](https://github.com/otto-nation/otto-workbench/commit/0569472cccb6fa3207ff4ea2bd9651844b54c0fe))


### Code Refactoring

* redirect tool events to stderr; misc cleanups ([#411](https://github.com/otto-nation/otto-workbench/issues/411)) ([55e85bd](https://github.com/otto-nation/otto-workbench/commit/55e85bd058a01a4b36ee68911f20d783e67e7421))
* **retro:** extract helpers; scope cleanup to consumed reviews ([#413](https://github.com/otto-nation/otto-workbench/issues/413)) ([57857b9](https://github.com/otto-nation/otto-workbench/commit/57857b9e6ed1c5780efc5322686709b086b10cc1))

## [1.31.2](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.31.1...otto-ai-tools-v1.31.2) (2026-06-27)


### Code Refactoring

* **pr-rebase:** replace fragmented resume logic with _drive_to_completion loop ([#405](https://github.com/otto-nation/otto-workbench/issues/405)) ([e0d0046](https://github.com/otto-nation/otto-workbench/commit/e0d0046c3e01a278b444841d1dd521d05513bf4c))

## [1.31.1](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.31.0...otto-ai-tools-v1.31.1) (2026-06-27)


### Bug Fixes

* **pr-rebase:** ignore untracked files in preflight dirty check ([#401](https://github.com/otto-nation/otto-workbench/issues/401)) ([45e529a](https://github.com/otto-nation/otto-workbench/commit/45e529a0c1863f4d3f8a982f70089d41d2b82be5))

## [1.31.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.30.0...otto-ai-tools-v1.31.0) (2026-06-26)


### Features

* **ci-check:** structural log extraction; headline surfacing in dashboard ([#398](https://github.com/otto-nation/otto-workbench/issues/398)) ([55fb271](https://github.com/otto-nation/otto-workbench/commit/55fb2718e2c166d50faced6023d312e099e954f6))

## [1.30.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.29.3...otto-ai-tools-v1.30.0) (2026-06-25)


### Features

* **ai:** Pi backend follow-ups — skills, extensions, steer, thinking, providers ([#390](https://github.com/otto-nation/otto-workbench/issues/390)) ([96b8dd5](https://github.com/otto-nation/otto-workbench/commit/96b8dd5b89cec09419de299d873c1c695ad069df))
* **review-threads:** post per-thread replies and summary after --fix ([#394](https://github.com/otto-nation/otto-workbench/issues/394)) ([ca8789d](https://github.com/otto-nation/otto-workbench/commit/ca8789def7afa13427afe838644f3cbaa4a5cdff))


### Bug Fixes

* **claude-review:** deterministic fix-pass summary via Finding diffing ([#396](https://github.com/otto-nation/otto-workbench/issues/396)) ([818a7ff](https://github.com/otto-nation/otto-workbench/commit/818a7ff11157cfb5187609295f08f627adcf7773))
* **review-threads:** strip markdown fences from AI triage JSON output ([#391](https://github.com/otto-nation/otto-workbench/issues/391)) ([80ccf14](https://github.com/otto-nation/otto-workbench/commit/80ccf14a6a17d51782dd1ab9b5148401c761c431))
* **trail:** add error coverage across pr CLI subcommands ([#393](https://github.com/otto-nation/otto-workbench/issues/393)) ([1fbc800](https://github.com/otto-nation/otto-workbench/commit/1fbc800cd395de4d633a94a75fc321c1a0c466fe))


### Code Refactoring

* **ai:** centralize stderr output in log module ([#397](https://github.com/otto-nation/otto-workbench/issues/397)) ([5bcf726](https://github.com/otto-nation/otto-workbench/commit/5bcf72674a9f4dcdd26b18cee01b30b3fdcd3929))

## [1.29.3](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.29.2...otto-ai-tools-v1.29.3) (2026-06-25)


### Bug Fixes

* **git:** sync gitignore.global entries into ~/.config/git/ignore ([#388](https://github.com/otto-nation/otto-workbench/issues/388)) ([83648fb](https://github.com/otto-nation/otto-workbench/commit/83648fb82202bc28282b9cb460b7ed15b835434b))
* **pr:** emit REVIEW_SUMMARY after successful review delegation ([#387](https://github.com/otto-nation/otto-workbench/issues/387)) ([d44c06e](https://github.com/otto-nation/otto-workbench/commit/d44c06e5d8854736ec002d7bdb6f2f76d6ef39e3))
* **review-threads:** cast line field to int before arithmetic comparison ([#386](https://github.com/otto-nation/otto-workbench/issues/386)) ([3e2f725](https://github.com/otto-nation/otto-workbench/commit/3e2f725a9de2cd3f8e3a32fac0b7ef0bc89b1bb5))


### Code Refactoring

* **ai:** extract AI backend abstraction for multi-backend support ([#383](https://github.com/otto-nation/otto-workbench/issues/383)) ([fa333e5](https://github.com/otto-nation/otto-workbench/commit/fa333e57411fdd68d1a43cd7bb21efe1273c0b95))

## [1.29.2](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.29.1...otto-ai-tools-v1.29.2) (2026-06-25)


### Bug Fixes

* **ai:** remove redundant WORKBENCH_DIR from migration ([#380](https://github.com/otto-nation/otto-workbench/issues/380)) ([ebce72a](https://github.com/otto-nation/otto-workbench/commit/ebce72a40364a87c22d4e5e7a0364244d65bc093))

## [1.29.1](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.29.0...otto-ai-tools-v1.29.1) (2026-06-25)


### Code Refactoring

* **skills:** remove redundant resolve-branch and direct script calls ([#377](https://github.com/otto-nation/otto-workbench/issues/377)) ([caf1e2c](https://github.com/otto-nation/otto-workbench/commit/caf1e2c1c077ab9f9cdb00e2f9e373d8f92ef439))

## [1.29.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.28.0...otto-ai-tools-v1.29.0) (2026-06-25)


### Features

* **trail:** add structured JSONL logging framework across AI scripts ([#375](https://github.com/otto-nation/otto-workbench/issues/375)) ([5d95f8d](https://github.com/otto-nation/otto-workbench/commit/5d95f8d8ebaae580f249edf9f273afa9985b3c60))


### Bug Fixes

* **claude-review:** evidence verification drops real findings; fix counting broken ([#372](https://github.com/otto-nation/otto-workbench/issues/372)) ([b3341d6](https://github.com/otto-nation/otto-workbench/commit/b3341d6a0250be4a612a9c2b616797b74f72479a))
* **hooks:** reduce false positives in brace expansion and branch guard ([#369](https://github.com/otto-nation/otto-workbench/issues/369)) ([cc0f4a6](https://github.com/otto-nation/otto-workbench/commit/cc0f4a6ebe7385a00dfa73e5a0eb2341d584e7e4))
* **pr-rebase:** resolve branch to worktree; default to --fix ([#374](https://github.com/otto-nation/otto-workbench/issues/374)) ([2e71b71](https://github.com/otto-nation/otto-workbench/commit/2e71b710adc86115b71001549ad0c7d0e71f58e4))


### Code Refactoring

* **ai:** migrate GitHub REST reads to GraphQL; share PRData ([#368](https://github.com/otto-nation/otto-workbench/issues/368)) ([349c822](https://github.com/otto-nation/otto-workbench/commit/349c82289bfbc4c8d40ff00048118de6c6e8c3de))

## [1.28.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.27.0...otto-ai-tools-v1.28.0) (2026-06-24)


### Features

* **pr-comments:** add --fix flag to triage and auto-fix review threads ([#360](https://github.com/otto-nation/otto-workbench/issues/360)) ([05227c4](https://github.com/otto-nation/otto-workbench/commit/05227c45f159a22f05321f599c626baf7b5ce3c4))


### Bug Fixes

* **ci-check:** treat skipped/cancelled runs as non-failures ([#365](https://github.com/otto-nation/otto-workbench/issues/365)) ([a827d11](https://github.com/otto-nation/otto-workbench/commit/a827d11ede7dea546fedf0f61a1b1a3df3daa6bb))
* **pr:** handle bare repos in pr_context.resolve() ([#364](https://github.com/otto-nation/otto-workbench/issues/364)) ([c315046](https://github.com/otto-nation/otto-workbench/commit/c3150468c25dfd91420771a2731569ef539e70b9))


### Code Refactoring

* **review:** pre-create output files before launching agents ([#363](https://github.com/otto-nation/otto-workbench/issues/363)) ([ec806b6](https://github.com/otto-nation/otto-workbench/commit/ec806b66cdef1138acecc314ffe9e83aa65ac495))
* **review:** strengthen convention-awareness in review prompts ([#366](https://github.com/otto-nation/otto-workbench/issues/366)) ([2d5495b](https://github.com/otto-nation/otto-workbench/commit/2d5495b68dc599f5ea7edabf0e41cfd4dccaeed0))

## [1.27.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.26.7...otto-ai-tools-v1.27.0) (2026-06-24)


### Features

* **pr-rebase:** add AI-assisted conflict resolution via claude -p ([#355](https://github.com/otto-nation/otto-workbench/issues/355)) ([f1028b7](https://github.com/otto-nation/otto-workbench/commit/f1028b73835506178d4eb8ef5471b66a171074fd))


### Bug Fixes

* **review:** preserve non-fallback worktrees after review ([#356](https://github.com/otto-nation/otto-workbench/issues/356)) ([6e66d01](https://github.com/otto-nation/otto-workbench/commit/6e66d01bcbedf6f41b596c6a88645a271fb2a18a))

## [1.26.7](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.26.6...otto-ai-tools-v1.26.7) (2026-06-23)


### Code Refactoring

* **pr:** eliminate double-dispatch; make pr the sole CLI entry point ([#351](https://github.com/otto-nation/otto-workbench/issues/351)) ([69ca53a](https://github.com/otto-nation/otto-workbench/commit/69ca53ab760197e1aa77c47c5c241af4c65b24ea))
* rename autoupdate agent to maintenance; fix gh auth ([#348](https://github.com/otto-nation/otto-workbench/issues/348)) ([27d56a7](https://github.com/otto-nation/otto-workbench/commit/27d56a7d5b989ae77b491f297250f2efb750ef44))

## [1.26.6](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.26.5...otto-ai-tools-v1.26.6) (2026-06-23)


### Bug Fixes

* **ci-check:** deduplicate re-runs per workflow ([#347](https://github.com/otto-nation/otto-workbench/issues/347)) ([9368e6a](https://github.com/otto-nation/otto-workbench/commit/9368e6a791c3597a154b1e09aa8495adaae6fd51))
* **skill:** use Write tool for pr-rebase conflict resolution ([#343](https://github.com/otto-nation/otto-workbench/issues/343)) ([a1a4d87](https://github.com/otto-nation/otto-workbench/commit/a1a4d872c70d935218cd397fa800ac0bd3e65cf1))


### Code Refactoring

* **claude-review:** eliminate duplicate resolution; use pr_context.resolve() everywhere ([#345](https://github.com/otto-nation/otto-workbench/issues/345)) ([1146332](https://github.com/otto-nation/otto-workbench/commit/1146332f84316b4947a2e1d7300796c3f55b432c))

## [1.26.5](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.26.4...otto-ai-tools-v1.26.5) (2026-06-23)


### Bug Fixes

* **pr:** forward only the user's original --pr or --branch flag ([#340](https://github.com/otto-nation/otto-workbench/issues/340)) ([e38529a](https://github.com/otto-nation/otto-workbench/commit/e38529a7ab17d6f7e1df2924669bc37eeb001f7b))

## [1.26.4](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.26.3...otto-ai-tools-v1.26.4) (2026-06-23)


### Bug Fixes

* **cli:** enforce --repo and --pr/--branch flag conventions ([#339](https://github.com/otto-nation/otto-workbench/issues/339)) ([9d4bc89](https://github.com/otto-nation/otto-workbench/commit/9d4bc89ea43db26291fc9e2876fd392ed3d25a21))
* **review-threads:** add --branch flag with resolve-branch support ([#335](https://github.com/otto-nation/otto-workbench/issues/335)) ([2a4b680](https://github.com/otto-nation/otto-workbench/commit/2a4b6806afb286837a7d542a676b11df2862bda6))


### Code Refactoring

* **claude-review:** convert from bash to Python ([#338](https://github.com/otto-nation/otto-workbench/issues/338)) ([36d3926](https://github.com/otto-nation/otto-workbench/commit/36d392659889b3a44a5d1ca4601bc32193ecc662))

## [1.26.3](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.26.2...otto-ai-tools-v1.26.3) (2026-06-22)


### Bug Fixes

* **pr:** parse global flags regardless of position after subcommand ([#330](https://github.com/otto-nation/otto-workbench/issues/330)) ([50c5198](https://github.com/otto-nation/otto-workbench/commit/50c51989ca8627d77f2dccf28a5e2497015bf67d))

## [1.26.2](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.26.1...otto-ai-tools-v1.26.2) (2026-06-22)


### Bug Fixes

* **pr:** pass --help through to delegated scripts ([#325](https://github.com/otto-nation/otto-workbench/issues/325)) ([7be1293](https://github.com/otto-nation/otto-workbench/commit/7be12936d546b1341adf193f14b0140dcebd0daf))
* **pr:** skip context resolution for help passthrough ([#328](https://github.com/otto-nation/otto-workbench/issues/328)) ([fc9a629](https://github.com/otto-nation/otto-workbench/commit/fc9a629def94e60e99972ecb2a5fcadb82188f4f))
* **review:** count fix-pass results from checkboxes instead of magic comment ([#329](https://github.com/otto-nation/otto-workbench/issues/329)) ([f8477c4](https://github.com/otto-nation/otto-workbench/commit/f8477c4a06831783ecea25d49eed06fe4b65ebb5))

## [1.26.1](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.26.0...otto-ai-tools-v1.26.1) (2026-06-22)


### Code Refactoring

* **pr:** migrate to script-owned state; improve CLI output ([#322](https://github.com/otto-nation/otto-workbench/issues/322)) ([a169747](https://github.com/otto-nation/otto-workbench/commit/a16974741e0e8e3abcdecee1de7a09682c3ffd37))

## [1.26.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.25.0...otto-ai-tools-v1.26.0) (2026-06-22)


### Features

* **pr:** add rebase subcommand with AI-assisted conflict resolution ([#313](https://github.com/otto-nation/otto-workbench/issues/313)) ([368acb1](https://github.com/otto-nation/otto-workbench/commit/368acb1697483f275ac31235270c459289ace886))


### Bug Fixes

* add PreToolUse hook to block command substitution in Bash tool ([#319](https://github.com/otto-nation/otto-workbench/issues/319)) ([743915d](https://github.com/otto-nation/otto-workbench/commit/743915d3d254f5e2495dc01e4b0d961100067cd6))
* **ci:** improve failure diagnosis with per-job log extraction ([#320](https://github.com/otto-nation/otto-workbench/issues/320)) ([2390f1a](https://github.com/otto-nation/otto-workbench/commit/2390f1a530a867fcff5aa49a07fdacb7ac9b165d))


### Code Refactoring

* move Bash tool permission patterns from git-operations to bash-tool ([#321](https://github.com/otto-nation/otto-workbench/issues/321)) ([787c895](https://github.com/otto-nation/otto-workbench/commit/787c89542b9c7a0d2901fde4569b8159081dc821))

## [1.25.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.24.0...otto-ai-tools-v1.25.0) (2026-06-22)


### Features

* add component registry, docker runtime selection, MCP manifests, and tooling improvements ([#12](https://github.com/otto-nation/otto-workbench/issues/12)) ([7297a13](https://github.com/otto-nation/otto-workbench/commit/7297a13aa82b830f572a567728f2b77309b09794))
* add post-install summaries and select_menu for component prompts ([#14](https://github.com/otto-nation/otto-workbench/issues/14)) ([42002c5](https://github.com/otto-nation/otto-workbench/commit/42002c58e631389e3784f5adabf7e0f263e6d243))
* add resolve-branch script for fuzzy branch resolution ([#263](https://github.com/otto-nation/otto-workbench/issues/263)) ([afd7e11](https://github.com/otto-nation/otto-workbench/commit/afd7e116c45375da7ea6016fd21d6c37474981e4))
* add review-thread-triage script for non-interactive PR thread classification ([#291](https://github.com/otto-nation/otto-workbench/issues/291)) ([073c0e5](https://github.com/otto-nation/otto-workbench/commit/073c0e5579ec3f6bc7f1fa6a0a182b91fb686def))
* add tool context registry, validation, and auto-generation ([#15](https://github.com/otto-nation/otto-workbench/issues/15)) ([7b724e5](https://github.com/otto-nation/otto-workbench/commit/7b724e5ca30e3f5f4af9e013ea5a1c41d29b1298))
* add unified pr CLI with state framework ([#298](https://github.com/otto-nation/otto-workbench/issues/298)) ([8e90905](https://github.com/otto-nation/otto-workbench/commit/8e90905f48f6a2c523cc7eeb5edea3a4ad6022c0))
* add user override layer; improve claude-review workflow ([#46](https://github.com/otto-nation/otto-workbench/issues/46)) ([fb024b8](https://github.com/otto-nation/otto-workbench/commit/fb024b863dc40c2fd696208a3736acbefe13f184))
* add wt-cleanup script; extract docs; simplify shell control flow ([#49](https://github.com/otto-nation/otto-workbench/issues/49)) ([815414a](https://github.com/otto-nation/otto-workbench/commit/815414abf6ce220f4b81dc2131697f7fa0d60e12))
* **ai:** add agents, serena-mcp script; prune redundant rules ([#34](https://github.com/otto-nation/otto-workbench/issues/34)) ([d60d22f](https://github.com/otto-nation/otto-workbench/commit/d60d22f4a466c20580076fe2f3b34fb625028085))
* **ai:** add Claude agents, dream skill, and hook syncing ([#33](https://github.com/otto-nation/otto-workbench/issues/33)) ([6deddfa](https://github.com/otto-nation/otto-workbench/commit/6deddfa68019133f29406463ccee287ff7341671))
* **ai:** add claude-review workflow; split tool context by loading mode ([#42](https://github.com/otto-nation/otto-workbench/issues/42)) ([24cb899](https://github.com/otto-nation/otto-workbench/commit/24cb899338210441ba417b3880bf0d2b2dfc4974))
* **ai:** add coding guidelines, rule templates, init/rules bins, and workbench sync ([#13](https://github.com/otto-nation/otto-workbench/issues/13)) ([4bb2827](https://github.com/otto-nation/otto-workbench/commit/4bb2827112d693da90f7adcea0c2eba6b6432b4f))
* **ai:** add config export with profile-based filtering ([#151](https://github.com/otto-nation/otto-workbench/issues/151)) ([f827a16](https://github.com/otto-nation/otto-workbench/commit/f827a16a4ea06c70f666b075247de4259308d1a1))
* **ai:** add headroom token compression as AI sub-tool ([#307](https://github.com/otto-nation/otto-workbench/issues/307)) ([c282a31](https://github.com/otto-nation/otto-workbench/commit/c282a317d03a1ab1393d0f8d18ab05c7dc738fdd))
* **ai:** add pr-review and analyze-project skills; generate public docs ([#38](https://github.com/otto-nation/otto-workbench/issues/38)) ([444e2f7](https://github.com/otto-nation/otto-workbench/commit/444e2f70dee9b6c6e79e25e7ca5a035bb9b566cb))
* **ai:** add second brain, memory backup, promote skill; harden CI and tooling ([#37](https://github.com/otto-nation/otto-workbench/issues/37)) ([0bfadd8](https://github.com/otto-nation/otto-workbench/commit/0bfadd896a6f4a5c52d428a133d37f16c8c5a780))
* **ai:** add setup script for Claude and Kiro tool configuration ([734de74](https://github.com/otto-nation/otto-workbench/commit/734de74d292edc5d2bfb5ba852c123da90da5a6b))
* **ai:** allow claude CLI and plugin script permissions ([#308](https://github.com/otto-nation/otto-workbench/issues/308)) ([32a4204](https://github.com/otto-nation/otto-workbench/commit/32a42040a2d110082d45521b1eb4e236ab17b828))
* **ai:** require source references in reviews; log local rule warnings ([#39](https://github.com/otto-nation/otto-workbench/issues/39)) ([1609fcc](https://github.com/otto-nation/otto-workbench/commit/1609fcc9bc8a4f3dc16a48db8cfe766c73395d35))
* **ai:** sync Claude settings, add MCPs, and skip already-installed items ([#11](https://github.com/otto-nation/otto-workbench/issues/11)) ([bba9fe8](https://github.com/otto-nation/otto-workbench/commit/bba9fe81cac5d56610477e7f8549820597061a76))
* **bin:** add gcloud-reauth script; claude-review usage stats ([#70](https://github.com/otto-nation/otto-workbench/issues/70)) ([651b058](https://github.com/otto-nation/otto-workbench/commit/651b058d10688fc63b90f3d5aa2364f9927ef57a))
* **brew:** add autoupdate tap; move review output to ~/.claude/reviews ([#41](https://github.com/otto-nation/otto-workbench/issues/41)) ([025d3c8](https://github.com/otto-nation/otto-workbench/commit/025d3c8bb8470a40eac76dade413678093326505))
* **ci-check:** add --branch flag; use resolve-branch in skills ([#285](https://github.com/otto-nation/otto-workbench/issues/285)) ([10e3705](https://github.com/otto-nation/otto-workbench/commit/10e37056480bdb2bbbe770895542d69f1e742bd1))
* **ci-failures:** add CI failure analysis skill and ci-check CLI ([#280](https://github.com/otto-nation/otto-workbench/issues/280)) ([365d021](https://github.com/otto-nation/otto-workbench/commit/365d021bdf5ef0d42c25dc8a8f2b207614c06ddc))
* **claude-review:** add --json-summary flag for structured output ([#132](https://github.com/otto-nation/otto-workbench/issues/132)) ([5008079](https://github.com/otto-nation/otto-workbench/commit/5008079e20c7e38f695727bd7d8705d8add5a985))
* **claude-review:** add --repo-dir flag for explicit repo path ([#137](https://github.com/otto-nation/otto-workbench/issues/137)) ([089cf46](https://github.com/otto-nation/otto-workbench/commit/089cf46db4575ae0c610fe7d858cff5e46eb2220))
* **claude-review:** add --resume flag; add validate-errexit lint ([#107](https://github.com/otto-nation/otto-workbench/issues/107)) ([69b8690](https://github.com/otto-nation/otto-workbench/commit/69b86909cef1f657537bf1df03baf2a88e9317a5))
* **claude-review:** add --resume to resume failed multi-phase reviews ([#106](https://github.com/otto-nation/otto-workbench/issues/106)) ([a068d06](https://github.com/otto-nation/otto-workbench/commit/a068d06b48910a508cb9e52292c65bde03e1c3ec))
* **claude-review:** add independent versioning and Homebrew formula ([#126](https://github.com/otto-nation/otto-workbench/issues/126)) ([f86f1c8](https://github.com/otto-nation/otto-workbench/commit/f86f1c8f680177e1358d7ba81fd16035251e4605))
* **claude-review:** add language idioms analysis phase ([#85](https://github.com/otto-nation/otto-workbench/issues/85)) ([8023c3f](https://github.com/otto-nation/otto-workbench/commit/8023c3f411e9c79405340a76375aaf95e89ab9a3))
* **claude-review:** add multi-phase parallel review for large PRs ([#69](https://github.com/otto-nation/otto-workbench/issues/69)) ([1540408](https://github.com/otto-nation/otto-workbench/commit/15404085c98d9e59bedd4477db827f65a892aaf2))
* **claude-review:** add preflight data collection to review agents ([#88](https://github.com/otto-nation/otto-workbench/issues/88)) ([8ee7bbd](https://github.com/otto-nation/otto-workbench/commit/8ee7bbde7cbba04dcf3fa510d243491a4801b3a1))
* **claude-review:** add rebuild subcommand ([#244](https://github.com/otto-nation/otto-workbench/issues/244)) ([657fe42](https://github.com/otto-nation/otto-workbench/commit/657fe421262dac20dc8d8f68e41c865d59adadf4))
* **claude-review:** add self-review mode for pre-PR code review ([#71](https://github.com/otto-nation/otto-workbench/issues/71)) ([8177b90](https://github.com/otto-nation/otto-workbench/commit/8177b90d08b7d5279b1d3fa8025813174623bd77))
* **claude-review:** add summary subcommand to regenerate JSON from disk ([#238](https://github.com/otto-nation/otto-workbench/issues/238)) ([6141a06](https://github.com/otto-nation/otto-workbench/commit/6141a064c452b459089fa288aef468b299975ad6))
* **claude-review:** dual-ref permalink resolution; consolidate GitHub API calls ([#147](https://github.com/otto-nation/otto-workbench/issues/147)) ([62e90dd](https://github.com/otto-nation/otto-workbench/commit/62e90ddea09581f5b714b8cedd6ff1850e7ec534))
* **claude-review:** folder storage, smart recovery, gc ([#192](https://github.com/otto-nation/otto-workbench/issues/192)) ([849f543](https://github.com/otto-nation/otto-workbench/commit/849f543bf3695fd3fcb13adc95bc76608d907b46))
* **claude-review:** incremental reviews; modular extraction ([#209](https://github.com/otto-nation/otto-workbench/issues/209)) ([2499a83](https://github.com/otto-nation/otto-workbench/commit/2499a8337e06b5ff71c27fa97b3b3a6699a5866c))
* **claude-review:** pre-flight checks; refactor(cli): noun-first ai syntax ([#80](https://github.com/otto-nation/otto-workbench/issues/80)) ([2516880](https://github.com/otto-nation/otto-workbench/commit/251688065e9e89cc3fd29aa2f6bfc935a1b8be1c))
* **claude-review:** wire reply threads into re-review prompts ([#309](https://github.com/otto-nation/otto-workbench/issues/309)) ([9d20ea8](https://github.com/otto-nation/otto-workbench/commit/9d20ea893908c98e46d244471d7dc799900537c4))
* **claude:** add --version/-V to all user-facing scripts ([#200](https://github.com/otto-nation/otto-workbench/issues/200)) ([4c14cd2](https://github.com/otto-nation/otto-workbench/commit/4c14cd24069709fd7188ec72334d8074b3b044fb))
* **claude:** manage additionalDirectories; close permission gaps ([#191](https://github.com/otto-nation/otto-workbench/issues/191)) ([88e6493](https://github.com/otto-nation/otto-workbench/commit/88e649336e820f415d0e50d64802b09dd7a81595))
* **commands:** add SSOT commands framework ([#196](https://github.com/otto-nation/otto-workbench/issues/196)) ([e397a38](https://github.com/otto-nation/otto-workbench/commit/e397a38b8bfed1285ee806a1c369f2b033cfbb96))
* **dream:** add dream-scan and dream-verify scripts ([#184](https://github.com/otto-nation/otto-workbench/issues/184)) ([13cf944](https://github.com/otto-nation/otto-workbench/commit/13cf944c5ae0c2fb5d582e9836706c89693e07bb))
* **hooks:** block absolute bin/local/ paths via PreToolUse hook ([#283](https://github.com/otto-nation/otto-workbench/issues/283)) ([e263175](https://github.com/otto-nation/otto-workbench/commit/e2631755145dfbb617db4573b24622a8a00b1b24))
* **hooks:** block brace expansion via PreToolUse hook ([#281](https://github.com/otto-nation/otto-workbench/issues/281)) ([3230133](https://github.com/otto-nation/otto-workbench/commit/3230133e4b94d1693a9255933e6c731ad5402665))
* **hooks:** block compound cd commands via PreToolUse hook ([#278](https://github.com/otto-nation/otto-workbench/issues/278)) ([e044d8a](https://github.com/otto-nation/otto-workbench/commit/e044d8aabe7403ddcf05d34900ab70c60aa919f6))
* **pr-comments:** add thread lifecycle tracking for multi-round reviews ([#226](https://github.com/otto-nation/otto-workbench/issues/226)) ([6b49dc6](https://github.com/otto-nation/otto-workbench/commit/6b49dc6fb2beb0abfd02fd189ba385da481aa17c))
* **promote:** add promote-scan script ([#185](https://github.com/otto-nation/otto-workbench/issues/185)) ([4d7659a](https://github.com/otto-nation/otto-workbench/commit/4d7659a501babbee251339da5fa5e18bd17b595c))
* **pr:** passthrough architecture; resolve-branch; triage and repair subcommands ([#299](https://github.com/otto-nation/otto-workbench/issues/299)) ([e956361](https://github.com/otto-nation/otto-workbench/commit/e9563619f6eace7b4031560fe77abf5d6e1dc06f))
* **registries:** add reverse bindir validation; register new tools ([#74](https://github.com/otto-nation/otto-workbench/issues/74)) ([4b17997](https://github.com/otto-nation/otto-workbench/commit/4b17997f300282283c02a278ef8c10322e2ab711))
* **registries:** derive Claude permissions from registry allow field ([#129](https://github.com/otto-nation/otto-workbench/issues/129)) ([e35c059](https://github.com/otto-nation/otto-workbench/commit/e35c05965b7c552c49413a087b82e5b80d387034))
* **retro:** add PR review feedback loop for rules improvement ([#224](https://github.com/otto-nation/otto-workbench/issues/224)) ([40ecb40](https://github.com/otto-nation/otto-workbench/commit/40ecb405e1903eea08b9fd2ad6d59f6215218924))
* **review-post:** migrate tests to pytest; add API layer coverage ([#118](https://github.com/otto-nation/otto-workbench/issues/118)) ([93a155d](https://github.com/otto-nation/otto-workbench/commit/93a155dca8d9ba0a8eaaf17da9ebe65249ee9f7b))
* **review:** add code-review angles, auto-fix, and retro integration ([#230](https://github.com/otto-nation/otto-workbench/issues/230)) ([677344b](https://github.com/otto-nation/otto-workbench/commit/677344b16c40dce99caeee0a5f33ab7679e9c16c))
* **review:** add evidence verification, stable IDs, and posted comment dedup ([#166](https://github.com/otto-nation/otto-workbench/issues/166)) ([003e97a](https://github.com/otto-nation/otto-workbench/commit/003e97aa4ab9b2ea99e3d7315ccd23ec83f71e5e))
* **review:** add head_sha, head_ref, base_ref, review_type to JSON summary ([#235](https://github.com/otto-nation/otto-workbench/issues/235)) ([7643455](https://github.com/otto-nation/otto-workbench/commit/7643455dd22c7b3b89c81eb152fe2a665dd794d9))
* **reviewer:** add test quality and convention evaluation criteria ([#119](https://github.com/otto-nation/otto-workbench/issues/119)) ([45830d0](https://github.com/otto-nation/otto-workbench/commit/45830d0894b3a7defb0a3cfbf27b1b1bd4fc641c))
* **review:** severity registry with posting routing ([#267](https://github.com/otto-nation/otto-workbench/issues/267)) ([de11526](https://github.com/otto-nation/otto-workbench/commit/de11526285dc561cfa1b4c7b7972fd1559795bf3))
* **rules:** add insights-driven rules; allow /tmp writes ([#103](https://github.com/otto-nation/otto-workbench/issues/103)) ([9b272ff](https://github.com/otto-nation/otto-workbench/commit/9b272ff2c4ded1bdf9e7349f8d94d3cc7cdbf191))
* **security:** add gitleaks scanning; extract git setup ([#19](https://github.com/otto-nation/otto-workbench/issues/19)) ([bfcd53d](https://github.com/otto-nation/otto-workbench/commit/bfcd53d54186b8eb5d86e4b534b3eae5bf70f7cf))
* **self-review-fix:** auto-commit applied fixes ([#270](https://github.com/otto-nation/otto-workbench/issues/270)) ([1399213](https://github.com/otto-nation/otto-workbench/commit/1399213ee8e306ab89edfc5503ccbc105ebf7383))
* **skills,permissions:** add Arguments sections; auto-sync permissions from registries ([#282](https://github.com/otto-nation/otto-workbench/issues/282)) ([51061d5](https://github.com/otto-nation/otto-workbench/commit/51061d5b003d357d623c9be02d07d59e15280a70))
* **skills:** add trigger/skip frontmatter fields to SKILL.md validation and docs ([#258](https://github.com/otto-nation/otto-workbench/issues/258)) ([c81cc89](https://github.com/otto-nation/otto-workbench/commit/c81cc89ef7a56ac19371701584878f27eda24302))
* **state:** add component installation state tracking ([#43](https://github.com/otto-nation/otto-workbench/issues/43)) ([a8b6f7a](https://github.com/otto-nation/otto-workbench/commit/a8b6f7a57f245365620e57b1dd884f0d2e599260))
* targeted install, worktrunk migration; improve review workflow ([#40](https://github.com/otto-nation/otto-workbench/issues/40)) ([2376694](https://github.com/otto-nation/otto-workbench/commit/23766940bca66dba159df4499085de3ca8617574))
* **terminals:** consolidate terminal config, add secret model bootstrap ([#26](https://github.com/otto-nation/otto-workbench/issues/26)) ([3f0c944](https://github.com/otto-nation/otto-workbench/commit/3f0c944f189890b37546af554a4cb73f223b2f52))
* **ui:** add install_file and copy_dir; replace symlinks with copies ([#28](https://github.com/otto-nation/otto-workbench/issues/28)) ([8991b32](https://github.com/otto-nation/otto-workbench/commit/8991b322330a559e6a8cee772788f9b288eb5a44))
* **validate-nesting:** extend nesting depth validator to all languages ([#108](https://github.com/otto-nation/otto-workbench/issues/108)) ([4565cf1](https://github.com/otto-nation/otto-workbench/commit/4565cf1a5e1286058f771ffe2eff7cb084eda877))
* workbench improvements — warnings, worktrees, component scripts, cleanup ([#36](https://github.com/otto-nation/otto-workbench/issues/36)) ([d357403](https://github.com/otto-nation/otto-workbench/commit/d357403221001ca8f4043636f62e6d62ff2b96b3))


### Bug Fixes

* allow bin/local/ scripts without permission prompts ([#277](https://github.com/otto-nation/otto-workbench/issues/277)) ([9640fec](https://github.com/otto-nation/otto-workbench/commit/9640fec7e706fe22335590ee4969df8d95ffc1f4))
* **anatomy:** support bare repo worktree layouts ([#274](https://github.com/otto-nation/otto-workbench/issues/274)) ([ddc7df5](https://github.com/otto-nation/otto-workbench/commit/ddc7df556d712fc6ac0710c314ac997c75819277))
* avoid bash parameter substitution in skill code blocks ([#237](https://github.com/otto-nation/otto-workbench/issues/237)) ([72e8d96](https://github.com/otto-nation/otto-workbench/commit/72e8d9604669f686ef93f88d6b1487a622e5b9b2))
* **ci-check:** fetch all workflow runs for latest commit ([#284](https://github.com/otto-nation/otto-workbench/issues/284)) ([1e9b149](https://github.com/otto-nation/otto-workbench/commit/1e9b14974b32ca05da3fb7fe81f24695c5fdb5aa))
* **ci:** add claude-config-release dispatch to homelab ([#186](https://github.com/otto-nation/otto-workbench/issues/186)) ([3a500e0](https://github.com/otto-nation/otto-workbench/commit/3a500e0b6748d4cd45ca9a4d2ca0d57a7a8c283e))
* **ci:** dynamically include all review scripts and Python libs in tarball ([#249](https://github.com/otto-nation/otto-workbench/issues/249)) ([f47388c](https://github.com/otto-nation/otto-workbench/commit/f47388cd8f9080cf8d0936110bce973ea0c2bc9b))
* **ci:** update build-claude-review-tarball to source lib/output.sh ([#172](https://github.com/otto-nation/otto-workbench/issues/172)) ([3a47e9f](https://github.com/otto-nation/otto-workbench/commit/3a47e9f5dbdc1b12e1b8e8217369813e4c927dfe))
* **claude-review:** add --repo alias; add bash safety note to reviewer agent ([#218](https://github.com/otto-nation/otto-workbench/issues/218)) ([edfaccf](https://github.com/otto-nation/otto-workbench/commit/edfaccf21ba725437d3fb6bd4acb451e0a44a4c3))
* **claude-review:** add --worktree alias for --repo-dir ([#213](https://github.com/otto-nation/otto-workbench/issues/213)) ([c1f167f](https://github.com/otto-nation/otto-workbench/commit/c1f167fe3bc7608d66d31874a3752ece4a15ae01))
* **claude-review:** add factual verification step to synthesis templates ([#155](https://github.com/otto-nation/otto-workbench/issues/155)) ([5290763](https://github.com/otto-nation/otto-workbench/commit/5290763a5cef6335935e8abedda8c70444ddbbfa))
* **claude-review:** add fix quality guidelines to reviewer agent ([#160](https://github.com/otto-nation/otto-workbench/issues/160)) ([cb3a9c4](https://github.com/otto-nation/otto-workbench/commit/cb3a9c4620874441f0921a13619065f29cb3aa3b))
* **claude-review:** add turn budget and efficiency constraints to reviewer ([#205](https://github.com/otto-nation/otto-workbench/issues/205)) ([acbc469](https://github.com/otto-nation/otto-workbench/commit/acbc469115e3b054a9b6e1fd95931580f4640f75))
* **claude-review:** add verification patterns to reduce false positives ([#154](https://github.com/otto-nation/otto-workbench/issues/154)) ([fc52f5b](https://github.com/otto-nation/otto-workbench/commit/fc52f5b5605ee52372d2cbecc082cb8d96f17ce7))
* **claude-review:** auto-resume failed groups; fix diagnostics ([#159](https://github.com/otto-nation/otto-workbench/issues/159)) ([377a19d](https://github.com/otto-nation/otto-workbench/commit/377a19dd1fc8e171b007d714814527948ccb3003))
* **claude-review:** clean stale fallback worktrees before creating new ones ([#142](https://github.com/otto-nation/otto-workbench/issues/142)) ([ecbc687](https://github.com/otto-nation/otto-workbench/commit/ecbc6873680aa2ede5a04e6f8353dd5774b00437))
* **claude-review:** cleanup flags, self-review fixes; speed up tests ([#255](https://github.com/otto-nation/otto-workbench/issues/255)) ([48ea5f5](https://github.com/otto-nation/otto-workbench/commit/48ea5f5d57318975a19d398381581315c27c558c))
* **claude-review:** conditional preflight packing; ERR trap; set -e function pitfall ([#104](https://github.com/otto-nation/otto-workbench/issues/104)) ([9f4196e](https://github.com/otto-nation/otto-workbench/commit/9f4196ee301010f07eeaaf6803cb4fdcf604ef5f))
* **claude-review:** disable skills during review to prevent context overflow ([#140](https://github.com/otto-nation/otto-workbench/issues/140)) ([0cabf46](https://github.com/otto-nation/otto-workbench/commit/0cabf46fc91057a830f9030fe9e454e571033984))
* **claude-review:** drop subject_type from inline comments ([#115](https://github.com/otto-nation/otto-workbench/issues/115)) ([64a792f](https://github.com/otto-nation/otto-workbench/commit/64a792ff37428f365cb66bb7569742364e758bb4))
* **claude-review:** fetch origin/base before computing diff ([#203](https://github.com/otto-nation/otto-workbench/issues/203)) ([d928b89](https://github.com/otto-nation/otto-workbench/commit/d928b898a4df1b025a8f379972eb2756f9ea4de2))
* **claude-review:** fix review posting; reduce synthesis context ([#114](https://github.com/otto-nation/otto-workbench/issues/114)) ([c4a8e51](https://github.com/otto-nation/otto-workbench/commit/c4a8e51ebe2e582b26bb7a966147f9f5c1b41bef))
* **claude-review:** fix runtime bugs; add comprehensive test coverage ([#216](https://github.com/otto-nation/otto-workbench/issues/216)) ([080205e](https://github.com/otto-nation/otto-workbench/commit/080205e456540933a4fc359ffbf669a79956b5ee))
* **claude-review:** handle corrupt prompt-stats.json from concurrent writes ([#247](https://github.com/otto-nation/otto-workbench/issues/247)) ([a378db9](https://github.com/otto-nation/otto-workbench/commit/a378db9050b1b5380561c07f23fd908a525daa95))
* **claude-review:** handle Ctrl+C gracefully across all scripts ([#122](https://github.com/otto-nation/otto-workbench/issues/122)) ([ba96585](https://github.com/otto-nation/otto-workbench/commit/ba96585425e299643eb28a2dc00f830dd70f1a48))
* **claude-review:** handle IsADirectoryError in _read_file_safe ([#252](https://github.com/otto-nation/otto-workbench/issues/252)) ([7961138](https://github.com/otto-nation/otto-workbench/commit/7961138325b54360573cb732ed198e8b31de0c46))
* **claude-review:** handle shallow clones in review pipeline ([#144](https://github.com/otto-nation/otto-workbench/issues/144)) ([2717fdd](https://github.com/otto-nation/otto-workbench/commit/2717fdd65d7304b8be092ebdd3b90d16b9b26c5d))
* **claude-review:** handle shallow clones; add metrics to JSON summary ([#146](https://github.com/otto-nation/otto-workbench/issues/146)) ([8585249](https://github.com/otto-nation/otto-workbench/commit/85852497a10e2843d875a9eb6faa3176df7462b6))
* **claude-review:** move self-review out of sensitive .claude/ dir ([#109](https://github.com/otto-nation/otto-workbench/issues/109)) ([8052151](https://github.com/otto-nation/otto-workbench/commit/8052151451c0d38bcd1ac89abadcb8304e696b8b))
* **claude-review:** preserve recent intermediates during gc ([#198](https://github.com/otto-nation/otto-workbench/issues/198)) ([9eabcc2](https://github.com/otto-nation/otto-workbench/commit/9eabcc23cbeb574406f6c00b7a1ac188a5c7020e))
* **claude-review:** prompt budget logging, group diff budget, scoped file budget ([#194](https://github.com/otto-nation/otto-workbench/issues/194)) ([e6b4fd7](https://github.com/otto-nation/otto-workbench/commit/e6b4fd7e35cf61d68749f60df864cfd7935047e9))
* **claude-review:** reduce prompt bloat with density-based file skipping ([#245](https://github.com/otto-nation/otto-workbench/issues/245)) ([54846bd](https://github.com/otto-nation/otto-workbench/commit/54846bd4ec0098adc5e256636741cce99d051524))
* **claude-review:** scale max_turns when density filter omits files ([#289](https://github.com/otto-nation/otto-workbench/issues/289)) ([263d79b](https://github.com/otto-nation/otto-workbench/commit/263d79be275a8c1041b73545d676663063de4ad5))
* **claude-review:** self-review archive, --force, and --no-post rule ([#100](https://github.com/otto-nation/otto-workbench/issues/100)) ([eeac16a](https://github.com/otto-nation/otto-workbench/commit/eeac16aa08dd38a9fa0747e5a3da88978688b597))
* **claude-review:** support --repo-dir with --self mode ([#164](https://github.com/otto-nation/otto-workbench/issues/164)) ([8ccacde](https://github.com/otto-nation/otto-workbench/commit/8ccacde2c27e71ec004fae309a8bd8ee23be326f))
* **claude-review:** tolerate h3/hyphenated severity headers; add severity calibration ([#208](https://github.com/otto-nation/otto-workbench/issues/208)) ([52b93f1](https://github.com/otto-nation/otto-workbench/commit/52b93f156906f8ea38215e075c0ccfa75daca572))
* **claude-review:** truncate diff for holistic/synthesis; fix dedup and formatting ([#157](https://github.com/otto-nation/otto-workbench/issues/157)) ([e45ca4b](https://github.com/otto-nation/otto-workbench/commit/e45ca4b2372151b9b893b5a2b0da7fbcea706d6b))
* **claude-review:** use "turns" not "tool calls" in turn budget sections ([#207](https://github.com/otto-nation/otto-workbench/issues/207)) ([0bde9f8](https://github.com/otto-nation/otto-workbench/commit/0bde9f8616954adc53d7ab0caaafc1750dc17f42))
* **claude-review:** use explicit prompt and skill file for post command ([#66](https://github.com/otto-nation/otto-workbench/issues/66)) ([792817d](https://github.com/otto-nation/otto-workbench/commit/792817d1168e434de4ed2fa46bed55c915d7bbb8))
* discover all bin scripts dynamically in tarball build ([#312](https://github.com/otto-nation/otto-workbench/issues/312)) ([3f379f6](https://github.com/otto-nation/otto-workbench/commit/3f379f68d5db22cebe18b034b1a07edf1ae40bcf))
* **dream,promote:** skip projects without memory/ in trigger checks ([#223](https://github.com/otto-nation/otto-workbench/issues/223)) ([cb45c51](https://github.com/otto-nation/otto-workbench/commit/cb45c51f0b18316e579b74cfa0ea971e2de02b6e))
* **dream:** per-project cooldowns; add lint-sweep and --draft flag ([#210](https://github.com/otto-nation/otto-workbench/issues/210)) ([d246939](https://github.com/otto-nation/otto-workbench/commit/d246939199ae9641ca8db93fa2503b3676c9be0e))
* enforce PR template usage via rule and hook ([#84](https://github.com/otto-nation/otto-workbench/issues/84)) ([ce9c45f](https://github.com/otto-nation/otto-workbench/commit/ce9c45f436c96fe9e5f6eb372279b0d2d34e127d))
* **git:** worktree hook delegation; refactor claude-review with poster agent ([#45](https://github.com/otto-nation/otto-workbench/issues/45)) ([c9c6126](https://github.com/otto-nation/otto-workbench/commit/c9c612653d82a90ed30b64416136e1704bbf52fc))
* **pr-comments:** add --repo-dir flag; improve skill discoverability ([#228](https://github.com/otto-nation/otto-workbench/issues/228)) ([e16530d](https://github.com/otto-nation/otto-workbench/commit/e16530da29fd84173814722bc6ada1075efca780))
* **pr-comments:** add TRIGGER/SKIP criteria to skill description ([#257](https://github.com/otto-nation/otto-workbench/issues/257)) ([98875dd](https://github.com/otto-nation/otto-workbench/commit/98875dd6c7bfc22f697d245881bd8b3959eea413))
* **pr-comments:** use resolve-branch for branch name arguments ([#290](https://github.com/otto-nation/otto-workbench/issues/290)) ([68f79db](https://github.com/otto-nation/otto-workbench/commit/68f79dba89d2978fbed57118bcba92868d6b866c))
* **review-orchestrate:** include uncommitted changes in self-review metadata ([#120](https://github.com/otto-nation/otto-workbench/issues/120)) ([51989ca](https://github.com/otto-nation/otto-workbench/commit/51989caccb8b96459c01313ac52eb3044b13687c))
* **review-post:** chunk large reviews; improve rate limit retry ([#117](https://github.com/otto-nation/otto-workbench/issues/117)) ([be85ce8](https://github.com/otto-nation/otto-workbench/commit/be85ce8842fcf57fa49b7fd553a176add6b001d5))
* **review-post:** dedup, orphan cleanup; retry failed groups ([#219](https://github.com/otto-nation/otto-workbench/issues/219)) ([7fc0977](https://github.com/otto-nation/otto-workbench/commit/7fc0977c9a33af4e09e84660606a168115a7ab72))
* **review-post:** derive default severity filter from SEVERITY_LABELS ([#94](https://github.com/otto-nation/otto-workbench/issues/94)) ([37f0db6](https://github.com/otto-nation/otto-workbench/commit/37f0db61c077d3850a69d81156a8478e8f4776f5))
* **review-post:** fallback to body-level when inline lines can't be resolved ([#116](https://github.com/otto-nation/otto-workbench/issues/116)) ([8c311c0](https://github.com/otto-nation/otto-workbench/commit/8c311c082fba8f0feea477be9429372637af5273))
* **review-post:** handle large PRs, minimized reviews, write errors ([#240](https://github.com/otto-nation/otto-workbench/issues/240)) ([9804ec1](https://github.com/otto-nation/otto-workbench/commit/9804ec16b162082366cda6f734e6bc5b0eea843a))
* **review-post:** prevent double-finalization from dropping finding body text ([#86](https://github.com/otto-nation/otto-workbench/issues/86)) ([574aa51](https://github.com/otto-nation/otto-workbench/commit/574aa5148c8ee63fb537755558dc18b914d668e1))
* **review-post:** propagate sidecar repo to args.repo ([#261](https://github.com/otto-nation/otto-workbench/issues/261)) ([12a6ec1](https://github.com/otto-nation/otto-workbench/commit/12a6ec11dc4ec342a1dd6384bb45e0cf3f48cafb))
* **review-post:** validate end_line against diff hunks for multi-line comments ([#121](https://github.com/otto-nation/otto-workbench/issues/121)) ([d02ad30](https://github.com/otto-nation/otto-workbench/commit/d02ad30556c9f3389b1f52aa7b9454b019443765))
* **review-post:** validate end_line against diff hunks for multi-line comments ([#131](https://github.com/otto-nation/otto-workbench/issues/131)) ([96c3862](https://github.com/otto-nation/otto-workbench/commit/96c38625cfa0f07d3d89ee83aaef1bfe22ec025f))
* **review:** add git-native worktree fallback for self-review branch switch ([#176](https://github.com/otto-nation/otto-workbench/issues/176)) ([74e197e](https://github.com/otto-nation/otto-workbench/commit/74e197eda0ca54e802cb9b3f1d7a0982e7fa18c0))
* **review:** add missing sys import in review_pipeline ([#234](https://github.com/otto-nation/otto-workbench/issues/234)) ([dc879d5](https://github.com/otto-nation/otto-workbench/commit/dc879d5eafd3ab64b5f1d42b0af58747278ee8d5))
* **review:** clean empty markers and fix stale verdict counts ([#178](https://github.com/otto-nation/otto-workbench/issues/178)) ([0b74247](https://github.com/otto-nation/otto-workbench/commit/0b7424749d3bd258965fcfaca0e3dd4687f7ded7))
* **review:** emit review_content in JSON summary; check agent exit codes ([#169](https://github.com/otto-nation/otto-workbench/issues/169)) ([c9bb122](https://github.com/otto-nation/otto-workbench/commit/c9bb1226fe6a3335f5660bccecfc47e87e3b70eb))
* **review:** grant write access to review file's parent directory ([#92](https://github.com/otto-nation/otto-workbench/issues/92)) ([1450e2b](https://github.com/otto-nation/otto-workbench/commit/1450e2bbc4e7922abc783a00c60ff78be905171b))
* **review:** improve orchestrate resilience for model errors and denied writes ([#183](https://github.com/otto-nation/otto-workbench/issues/183)) ([e4ae310](https://github.com/otto-nation/otto-workbench/commit/e4ae3105631969fdcd2196e1c4fc579980057b33))
* **review:** improve review-post resilience for SHA drift and path-less findings ([#188](https://github.com/otto-nation/otto-workbench/issues/188)) ([50563d2](https://github.com/otto-nation/otto-workbench/commit/50563d262f1313dab55077c9f2ae62a033927706))
* **review:** improve synthesis resilience; eliminate permission denials ([#189](https://github.com/otto-nation/otto-workbench/issues/189)) ([151df4f](https://github.com/otto-nation/otto-workbench/commit/151df4fd15cae380e013d29776be42985ab18717))
* **review:** support REPO_DIR env var for cross-repo usage ([#180](https://github.com/otto-nation/otto-workbench/issues/180)) ([1a3bcdb](https://github.com/otto-nation/otto-workbench/commit/1a3bcdb5e6473af6d603ad285a151ac02a9f6b97))
* **review:** use --bare for agent invocations to skip plugins and hooks ([#174](https://github.com/otto-nation/otto-workbench/issues/174)) ([c49d495](https://github.com/otto-nation/otto-workbench/commit/c49d495bf3a421877970988519e498d52a8cebeb))
* **self-review-fix:** avoid permission prompt from command substitution ([#268](https://github.com/otto-nation/otto-workbench/issues/268)) ([ce01a6a](https://github.com/otto-nation/otto-workbench/commit/ce01a6a51cbffb49f16fdd964c22ba3caab0ad58))
* **self-review-fix:** prevent permission prompts from fix-pass agent ([#269](https://github.com/otto-nation/otto-workbench/issues/269)) ([1761394](https://github.com/otto-nation/otto-workbench/commit/1761394df087fe467ba9ebb46f05f9d3d32efe37))
* **self-review-fix:** prevent stale reporting and fix-pass comment misplacement ([#266](https://github.com/otto-nation/otto-workbench/issues/266)) ([a55fd61](https://github.com/otto-nation/otto-workbench/commit/a55fd61fb82b8bbb638e2b9255dec74993916ce3))
* **skills:** escape PR reply bodies with heredoc pipe ([#110](https://github.com/otto-nation/otto-workbench/issues/110)) ([d2ac529](https://github.com/otto-nation/otto-workbench/commit/d2ac5294151f2d545ea0df1c38eea42d28411069))
* **skills:** handle bare repos and permission prompts in self-review-fix ([#242](https://github.com/otto-nation/otto-workbench/issues/242)) ([15ef7bc](https://github.com/otto-nation/otto-workbench/commit/15ef7bc1b605f86438ab95c862a8fd476b5276cf))
* **skills:** move sensitive-path file ops into scripts ([#221](https://github.com/otto-nation/otto-workbench/issues/221)) ([e20f765](https://github.com/otto-nation/otto-workbench/commit/e20f765e57a04706ae43197233d68807ab5d9846))
* **zed:** use python3 JSONC parser; add brew fpath before compinit ([#30](https://github.com/otto-nation/otto-workbench/issues/30)) ([7fcd622](https://github.com/otto-nation/otto-workbench/commit/7fcd622d6286ad29138d47358246b39e24df30cb))


### Performance Improvements

* **claude-review:** budget controls, scoped diffs; reduce review cost ([#98](https://github.com/otto-nation/otto-workbench/issues/98)) ([4f09035](https://github.com/otto-nation/otto-workbench/commit/4f090352c340725e4a5a2fa857edd1e0b0f5b63e))
* **claude-review:** optimize review pipeline and add metadata tracking ([#95](https://github.com/otto-nation/otto-workbench/issues/95)) ([8ea407a](https://github.com/otto-nation/otto-workbench/commit/8ea407a1fe873d3570a3e99733954580d6d173f8))


### Code Refactoring

* add context field to registries; clean up stale references ([#68](https://github.com/otto-nation/otto-workbench/issues/68)) ([0a52e1d](https://github.com/otto-nation/otto-workbench/commit/0a52e1d2090134989a805e37aa395f499d55c660))
* **ai:** modularize AI lib, harden scripts, add component validation ([#25](https://github.com/otto-nation/otto-workbench/issues/25)) ([dbf7b8c](https://github.com/otto-nation/otto-workbench/commit/dbf7b8ccfbe206e5ff02f5f72bafe8daa802f99d))
* **auto-tasks:** run dream/promote/retro as headless sessions ([#233](https://github.com/otto-nation/otto-workbench/issues/233)) ([f889f3d](https://github.com/otto-nation/otto-workbench/commit/f889f3d4a8318b38480e6839e36598f9e6f159be))
* **brew:** reorganize optional Brewfiles into category subdirs ([#24](https://github.com/otto-nation/otto-workbench/issues/24)) ([db269c8](https://github.com/otto-nation/otto-workbench/commit/db269c8694e613362da90dea9c7682f6fb5b7fdf))
* centralize output helpers; move usage text to usage() ([#72](https://github.com/otto-nation/otto-workbench/issues/72)) ([a73fba6](https://github.com/otto-nation/otto-workbench/commit/a73fba6f83d395dcdd1ca2691cf8768bdbee0394))
* **claude-review:** extract post logic into review-post ([#83](https://github.com/otto-nation/otto-workbench/issues/83)) ([5f58538](https://github.com/otto-nation/otto-workbench/commit/5f58538612749f996348824ef276fad0190947d1))
* **claude-review:** extract review-post into library modules ([#214](https://github.com/otto-nation/otto-workbench/issues/214)) ([719d9ee](https://github.com/otto-nation/otto-workbench/commit/719d9eec252c6f0553fad281e73caef645c59fe0))
* **claude:** replace poster agent with /pr-review skill ([#63](https://github.com/otto-nation/otto-workbench/issues/63)) ([42a6b69](https://github.com/otto-nation/otto-workbench/commit/42a6b698a477bcf1ef87f5893727ae9470792bd1))
* **cli:** move claude and override commands under ai subcommand ([#75](https://github.com/otto-nation/otto-workbench/issues/75)) ([c7f7e4c](https://github.com/otto-nation/otto-workbench/commit/c7f7e4c7011797b5bb8f61b6cef683a157d44d21))
* **lib:** centralize constants, expand docs and test setup ([#16](https://github.com/otto-nation/otto-workbench/issues/16)) ([f28a167](https://github.com/otto-nation/otto-workbench/commit/f28a167e02c13ca90b0c3a2a6d0ada06c174b5bc))
* **registries:** define tool entry interface; require allow and context ([#292](https://github.com/otto-nation/otto-workbench/issues/292)) ([57f17f6](https://github.com/otto-nation/otto-workbench/commit/57f17f6e1f29854d318ccfd083197f4e42caa04f))
* **registries:** rename allow→permission, context→visibility; enforce conditional fields ([#296](https://github.com/otto-nation/otto-workbench/issues/296)) ([4718b3d](https://github.com/otto-nation/otto-workbench/commit/4718b3d29005429229ed0c85770a123c2bab9a4d))
* relocate user overrides from repo to XDG state dir ([7794730](https://github.com/otto-nation/otto-workbench/commit/77947302744edcd77826856122d60176ac461aab))
* rename claude-review tarball to otto-ai-tools ([#314](https://github.com/otto-nation/otto-workbench/issues/314)) ([c4ed937](https://github.com/otto-nation/otto-workbench/commit/c4ed937648add4e4f4b418b4aaae70d77f637c4b))
* replace fragile ../ paths; centralize constants ([#254](https://github.com/otto-nation/otto-workbench/issues/254)) ([285d750](https://github.com/otto-nation/otto-workbench/commit/285d750bf8a02d26e5e17388960d85000c0fdde5))
* restructure lib modules; add per-org GH_TOKEN; harden scripts ([#31](https://github.com/otto-nation/otto-workbench/issues/31)) ([b757b32](https://github.com/otto-nation/otto-workbench/commit/b757b32e34f78fa94bb6bb56f02c9d2900573f6b))
* **review:** absorb pr-comments-status into claude-review threads ([#232](https://github.com/otto-nation/otto-workbench/issues/232)) ([f23248d](https://github.com/otto-nation/otto-workbench/commit/f23248d756c9356033d8d23efaff416b124894ba))
* **self-review-fix:** use git remote instead of gh CLI for repo name ([#265](https://github.com/otto-nation/otto-workbench/issues/265)) ([7fc5c57](https://github.com/otto-nation/otto-workbench/commit/7fc5c57bd8131a6a84aba4af92b1b8ec2c2cf50c))
* **workbench:** centralize paths, modularize steps, auto-discover components ([#23](https://github.com/otto-nation/otto-workbench/issues/23)) ([bf61b3b](https://github.com/otto-nation/otto-workbench/commit/bf61b3bb82783d238c17685749816c2854df27d4))
* **workbench:** reorganize scripts, env management; add nesting validator and GPG setup ([#48](https://github.com/otto-nation/otto-workbench/issues/48)) ([fff0b20](https://github.com/otto-nation/otto-workbench/commit/fff0b20c64a1596a992e61d8e56920e255137432))

## [1.24.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.23.0...claude-review-v1.24.0) (2026-06-22)


### Features

* **pr:** passthrough architecture; resolve-branch; triage and repair subcommands ([#299](https://github.com/otto-nation/otto-workbench/issues/299)) ([e956361](https://github.com/otto-nation/otto-workbench/commit/e9563619f6eace7b4031560fe77abf5d6e1dc06f))

## [1.23.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.22.0...claude-review-v1.23.0) (2026-06-20)


### Features

* add unified pr CLI with state framework ([#298](https://github.com/otto-nation/otto-workbench/issues/298)) ([8e90905](https://github.com/otto-nation/otto-workbench/commit/8e90905f48f6a2c523cc7eeb5edea3a4ad6022c0))


### Code Refactoring

* **registries:** rename allow→permission, context→visibility; enforce conditional fields ([#296](https://github.com/otto-nation/otto-workbench/issues/296)) ([4718b3d](https://github.com/otto-nation/otto-workbench/commit/4718b3d29005429229ed0c85770a123c2bab9a4d))

## [1.22.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.21.0...claude-review-v1.22.0) (2026-06-20)


### Features

* add review-thread-triage script for non-interactive PR thread classification ([#291](https://github.com/otto-nation/otto-workbench/issues/291)) ([073c0e5](https://github.com/otto-nation/otto-workbench/commit/073c0e5579ec3f6bc7f1fa6a0a182b91fb686def))
* **ci-check:** add --branch flag; use resolve-branch in skills ([#285](https://github.com/otto-nation/otto-workbench/issues/285)) ([10e3705](https://github.com/otto-nation/otto-workbench/commit/10e37056480bdb2bbbe770895542d69f1e742bd1))
* **ci-failures:** add CI failure analysis skill and ci-check CLI ([#280](https://github.com/otto-nation/otto-workbench/issues/280)) ([365d021](https://github.com/otto-nation/otto-workbench/commit/365d021bdf5ef0d42c25dc8a8f2b207614c06ddc))
* **hooks:** block absolute bin/local/ paths via PreToolUse hook ([#283](https://github.com/otto-nation/otto-workbench/issues/283)) ([e263175](https://github.com/otto-nation/otto-workbench/commit/e2631755145dfbb617db4573b24622a8a00b1b24))
* **hooks:** block brace expansion via PreToolUse hook ([#281](https://github.com/otto-nation/otto-workbench/issues/281)) ([3230133](https://github.com/otto-nation/otto-workbench/commit/3230133e4b94d1693a9255933e6c731ad5402665))
* **hooks:** block compound cd commands via PreToolUse hook ([#278](https://github.com/otto-nation/otto-workbench/issues/278)) ([e044d8a](https://github.com/otto-nation/otto-workbench/commit/e044d8aabe7403ddcf05d34900ab70c60aa919f6))
* **skills,permissions:** add Arguments sections; auto-sync permissions from registries ([#282](https://github.com/otto-nation/otto-workbench/issues/282)) ([51061d5](https://github.com/otto-nation/otto-workbench/commit/51061d5b003d357d623c9be02d07d59e15280a70))


### Bug Fixes

* allow bin/local/ scripts without permission prompts ([#277](https://github.com/otto-nation/otto-workbench/issues/277)) ([9640fec](https://github.com/otto-nation/otto-workbench/commit/9640fec7e706fe22335590ee4969df8d95ffc1f4))
* **anatomy:** support bare repo worktree layouts ([#274](https://github.com/otto-nation/otto-workbench/issues/274)) ([ddc7df5](https://github.com/otto-nation/otto-workbench/commit/ddc7df556d712fc6ac0710c314ac997c75819277))
* **ci-check:** fetch all workflow runs for latest commit ([#284](https://github.com/otto-nation/otto-workbench/issues/284)) ([1e9b149](https://github.com/otto-nation/otto-workbench/commit/1e9b14974b32ca05da3fb7fe81f24695c5fdb5aa))
* **claude-review:** scale max_turns when density filter omits files ([#289](https://github.com/otto-nation/otto-workbench/issues/289)) ([263d79b](https://github.com/otto-nation/otto-workbench/commit/263d79be275a8c1041b73545d676663063de4ad5))
* **pr-comments:** use resolve-branch for branch name arguments ([#290](https://github.com/otto-nation/otto-workbench/issues/290)) ([68f79db](https://github.com/otto-nation/otto-workbench/commit/68f79dba89d2978fbed57118bcba92868d6b866c))


### Code Refactoring

* **registries:** define tool entry interface; require allow and context ([#292](https://github.com/otto-nation/otto-workbench/issues/292)) ([57f17f6](https://github.com/otto-nation/otto-workbench/commit/57f17f6e1f29854d318ccfd083197f4e42caa04f))

## [1.21.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.20.0...claude-review-v1.21.0) (2026-06-17)


### Features

* **self-review-fix:** auto-commit applied fixes ([#270](https://github.com/otto-nation/otto-workbench/issues/270)) ([1399213](https://github.com/otto-nation/otto-workbench/commit/1399213ee8e306ab89edfc5503ccbc105ebf7383))

## [1.20.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.19.1...claude-review-v1.20.0) (2026-06-17)


### Features

* add resolve-branch script for fuzzy branch resolution ([#263](https://github.com/otto-nation/otto-workbench/issues/263)) ([afd7e11](https://github.com/otto-nation/otto-workbench/commit/afd7e116c45375da7ea6016fd21d6c37474981e4))
* **review:** severity registry with posting routing ([#267](https://github.com/otto-nation/otto-workbench/issues/267)) ([de11526](https://github.com/otto-nation/otto-workbench/commit/de11526285dc561cfa1b4c7b7972fd1559795bf3))


### Bug Fixes

* **self-review-fix:** avoid permission prompt from command substitution ([#268](https://github.com/otto-nation/otto-workbench/issues/268)) ([ce01a6a](https://github.com/otto-nation/otto-workbench/commit/ce01a6a51cbffb49f16fdd964c22ba3caab0ad58))
* **self-review-fix:** prevent permission prompts from fix-pass agent ([#269](https://github.com/otto-nation/otto-workbench/issues/269)) ([1761394](https://github.com/otto-nation/otto-workbench/commit/1761394df087fe467ba9ebb46f05f9d3d32efe37))
* **self-review-fix:** prevent stale reporting and fix-pass comment misplacement ([#266](https://github.com/otto-nation/otto-workbench/issues/266)) ([a55fd61](https://github.com/otto-nation/otto-workbench/commit/a55fd61fb82b8bbb638e2b9255dec74993916ce3))


### Code Refactoring

* **self-review-fix:** use git remote instead of gh CLI for repo name ([#265](https://github.com/otto-nation/otto-workbench/issues/265)) ([7fc5c57](https://github.com/otto-nation/otto-workbench/commit/7fc5c57bd8131a6a84aba4af92b1b8ec2c2cf50c))

## [1.19.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.19.0...claude-review-v1.19.1) (2026-06-16)


### Bug Fixes

* **review-post:** propagate sidecar repo to args.repo ([#261](https://github.com/otto-nation/otto-workbench/issues/261)) ([12a6ec1](https://github.com/otto-nation/otto-workbench/commit/12a6ec11dc4ec342a1dd6384bb45e0cf3f48cafb))

## [1.19.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.18.1...claude-review-v1.19.0) (2026-06-16)


### Features

* **skills:** add trigger/skip frontmatter fields to SKILL.md validation and docs ([#258](https://github.com/otto-nation/otto-workbench/issues/258)) ([c81cc89](https://github.com/otto-nation/otto-workbench/commit/c81cc89ef7a56ac19371701584878f27eda24302))


### Bug Fixes

* **claude-review:** cleanup flags, self-review fixes; speed up tests ([#255](https://github.com/otto-nation/otto-workbench/issues/255)) ([48ea5f5](https://github.com/otto-nation/otto-workbench/commit/48ea5f5d57318975a19d398381581315c27c558c))
* **claude-review:** handle IsADirectoryError in _read_file_safe ([#252](https://github.com/otto-nation/otto-workbench/issues/252)) ([7961138](https://github.com/otto-nation/otto-workbench/commit/7961138325b54360573cb732ed198e8b31de0c46))
* **pr-comments:** add TRIGGER/SKIP criteria to skill description ([#257](https://github.com/otto-nation/otto-workbench/issues/257)) ([98875dd](https://github.com/otto-nation/otto-workbench/commit/98875dd6c7bfc22f697d245881bd8b3959eea413))


### Code Refactoring

* replace fragile ../ paths; centralize constants ([#254](https://github.com/otto-nation/otto-workbench/issues/254)) ([285d750](https://github.com/otto-nation/otto-workbench/commit/285d750bf8a02d26e5e17388960d85000c0fdde5))

## [1.18.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.18.0...claude-review-v1.18.1) (2026-06-16)


### Bug Fixes

* **ci:** dynamically include all review scripts and Python libs in tarball ([#249](https://github.com/otto-nation/otto-workbench/issues/249)) ([f47388c](https://github.com/otto-nation/otto-workbench/commit/f47388cd8f9080cf8d0936110bce973ea0c2bc9b))

## [1.18.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.17.2...claude-review-v1.18.0) (2026-06-16)


### Features

* **claude-review:** add rebuild subcommand ([#244](https://github.com/otto-nation/otto-workbench/issues/244)) ([657fe42](https://github.com/otto-nation/otto-workbench/commit/657fe421262dac20dc8d8f68e41c865d59adadf4))


### Bug Fixes

* **claude-review:** handle corrupt prompt-stats.json from concurrent writes ([#247](https://github.com/otto-nation/otto-workbench/issues/247)) ([a378db9](https://github.com/otto-nation/otto-workbench/commit/a378db9050b1b5380561c07f23fd908a525daa95))
* **claude-review:** reduce prompt bloat with density-based file skipping ([#245](https://github.com/otto-nation/otto-workbench/issues/245)) ([54846bd](https://github.com/otto-nation/otto-workbench/commit/54846bd4ec0098adc5e256636741cce99d051524))

## [1.17.2](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.17.1...claude-review-v1.17.2) (2026-06-15)


### Bug Fixes

* **skills:** handle bare repos and permission prompts in self-review-fix ([#242](https://github.com/otto-nation/otto-workbench/issues/242)) ([15ef7bc](https://github.com/otto-nation/otto-workbench/commit/15ef7bc1b605f86438ab95c862a8fd476b5276cf))

## [1.17.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.17.0...claude-review-v1.17.1) (2026-06-15)


### Bug Fixes

* **review-post:** handle large PRs, minimized reviews, write errors ([#240](https://github.com/otto-nation/otto-workbench/issues/240)) ([9804ec1](https://github.com/otto-nation/otto-workbench/commit/9804ec16b162082366cda6f734e6bc5b0eea843a))

## [1.17.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.16.0...claude-review-v1.17.0) (2026-06-15)


### Features

* **claude-review:** add summary subcommand to regenerate JSON from disk ([#238](https://github.com/otto-nation/otto-workbench/issues/238)) ([6141a06](https://github.com/otto-nation/otto-workbench/commit/6141a064c452b459089fa288aef468b299975ad6))

## [1.16.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.15.0...claude-review-v1.16.0) (2026-06-15)


### Features

* **review:** add head_sha, head_ref, base_ref, review_type to JSON summary ([#235](https://github.com/otto-nation/otto-workbench/issues/235)) ([7643455](https://github.com/otto-nation/otto-workbench/commit/7643455dd22c7b3b89c81eb152fe2a665dd794d9))


### Bug Fixes

* avoid bash parameter substitution in skill code blocks ([#237](https://github.com/otto-nation/otto-workbench/issues/237)) ([72e8d96](https://github.com/otto-nation/otto-workbench/commit/72e8d9604669f686ef93f88d6b1487a622e5b9b2))

## [1.15.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.14.0...claude-review-v1.15.0) (2026-06-15)


### Features

* **review:** add code-review angles, auto-fix, and retro integration ([#230](https://github.com/otto-nation/otto-workbench/issues/230)) ([677344b](https://github.com/otto-nation/otto-workbench/commit/677344b16c40dce99caeee0a5f33ab7679e9c16c))


### Bug Fixes

* **pr-comments:** add --repo-dir flag; improve skill discoverability ([#228](https://github.com/otto-nation/otto-workbench/issues/228)) ([e16530d](https://github.com/otto-nation/otto-workbench/commit/e16530da29fd84173814722bc6ada1075efca780))
* **review:** add missing sys import in review_pipeline ([#234](https://github.com/otto-nation/otto-workbench/issues/234)) ([dc879d5](https://github.com/otto-nation/otto-workbench/commit/dc879d5eafd3ab64b5f1d42b0af58747278ee8d5))


### Code Refactoring

* **auto-tasks:** run dream/promote/retro as headless sessions ([#233](https://github.com/otto-nation/otto-workbench/issues/233)) ([f889f3d](https://github.com/otto-nation/otto-workbench/commit/f889f3d4a8318b38480e6839e36598f9e6f159be))
* **review:** absorb pr-comments-status into claude-review threads ([#232](https://github.com/otto-nation/otto-workbench/issues/232)) ([f23248d](https://github.com/otto-nation/otto-workbench/commit/f23248d756c9356033d8d23efaff416b124894ba))

## [1.14.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.13.0...claude-review-v1.14.0) (2026-06-15)


### Features

* **pr-comments:** add thread lifecycle tracking for multi-round reviews ([#226](https://github.com/otto-nation/otto-workbench/issues/226)) ([6b49dc6](https://github.com/otto-nation/otto-workbench/commit/6b49dc6fb2beb0abfd02fd189ba385da481aa17c))

## [1.13.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.12.2...claude-review-v1.13.0) (2026-06-12)


### Features

* **retro:** add PR review feedback loop for rules improvement ([#224](https://github.com/otto-nation/otto-workbench/issues/224)) ([40ecb40](https://github.com/otto-nation/otto-workbench/commit/40ecb405e1903eea08b9fd2ad6d59f6215218924))


### Bug Fixes

* **dream,promote:** skip projects without memory/ in trigger checks ([#223](https://github.com/otto-nation/otto-workbench/issues/223)) ([cb45c51](https://github.com/otto-nation/otto-workbench/commit/cb45c51f0b18316e579b74cfa0ea971e2de02b6e))
* **skills:** move sensitive-path file ops into scripts ([#221](https://github.com/otto-nation/otto-workbench/issues/221)) ([e20f765](https://github.com/otto-nation/otto-workbench/commit/e20f765e57a04706ae43197233d68807ab5d9846))

## [1.12.2](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.12.1...claude-review-v1.12.2) (2026-06-12)


### Bug Fixes

* **claude-review:** add --repo alias; add bash safety note to reviewer agent ([#218](https://github.com/otto-nation/otto-workbench/issues/218)) ([edfaccf](https://github.com/otto-nation/otto-workbench/commit/edfaccf21ba725437d3fb6bd4acb451e0a44a4c3))
* **review-post:** dedup, orphan cleanup; retry failed groups ([#219](https://github.com/otto-nation/otto-workbench/issues/219)) ([7fc0977](https://github.com/otto-nation/otto-workbench/commit/7fc0977c9a33af4e09e84660606a168115a7ab72))

## [1.12.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.12.0...claude-review-v1.12.1) (2026-06-11)


### Bug Fixes

* **claude-review:** fix runtime bugs; add comprehensive test coverage ([#216](https://github.com/otto-nation/otto-workbench/issues/216)) ([080205e](https://github.com/otto-nation/otto-workbench/commit/080205e456540933a4fc359ffbf669a79956b5ee))

## [1.12.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.11.2...claude-review-v1.12.0) (2026-06-11)


### Features

* **claude-review:** incremental reviews; modular extraction ([#209](https://github.com/otto-nation/otto-workbench/issues/209)) ([2499a83](https://github.com/otto-nation/otto-workbench/commit/2499a8337e06b5ff71c27fa97b3b3a6699a5866c))


### Bug Fixes

* **claude-review:** add --worktree alias for --repo-dir ([#213](https://github.com/otto-nation/otto-workbench/issues/213)) ([c1f167f](https://github.com/otto-nation/otto-workbench/commit/c1f167fe3bc7608d66d31874a3752ece4a15ae01))
* **dream:** per-project cooldowns; add lint-sweep and --draft flag ([#210](https://github.com/otto-nation/otto-workbench/issues/210)) ([d246939](https://github.com/otto-nation/otto-workbench/commit/d246939199ae9641ca8db93fa2503b3676c9be0e))


### Code Refactoring

* **claude-review:** extract review-post into library modules ([#214](https://github.com/otto-nation/otto-workbench/issues/214)) ([719d9ee](https://github.com/otto-nation/otto-workbench/commit/719d9eec252c6f0553fad281e73caef645c59fe0))

## [1.11.2](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.11.1...claude-review-v1.11.2) (2026-06-10)


### Bug Fixes

* **claude-review:** add turn budget and efficiency constraints to reviewer ([#205](https://github.com/otto-nation/otto-workbench/issues/205)) ([acbc469](https://github.com/otto-nation/otto-workbench/commit/acbc469115e3b054a9b6e1fd95931580f4640f75))
* **claude-review:** tolerate h3/hyphenated severity headers; add severity calibration ([#208](https://github.com/otto-nation/otto-workbench/issues/208)) ([52b93f1](https://github.com/otto-nation/otto-workbench/commit/52b93f156906f8ea38215e075c0ccfa75daca572))
* **claude-review:** use "turns" not "tool calls" in turn budget sections ([#207](https://github.com/otto-nation/otto-workbench/issues/207)) ([0bde9f8](https://github.com/otto-nation/otto-workbench/commit/0bde9f8616954adc53d7ab0caaafc1750dc17f42))

## [1.11.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.11.0...claude-review-v1.11.1) (2026-06-10)


### Bug Fixes

* **claude-review:** fetch origin/base before computing diff ([#203](https://github.com/otto-nation/otto-workbench/issues/203)) ([d928b89](https://github.com/otto-nation/otto-workbench/commit/d928b898a4df1b025a8f379972eb2756f9ea4de2))

## [1.11.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.10.0...claude-review-v1.11.0) (2026-06-09)


### Features

* **claude:** add --version/-V to all user-facing scripts ([#200](https://github.com/otto-nation/otto-workbench/issues/200)) ([4c14cd2](https://github.com/otto-nation/otto-workbench/commit/4c14cd24069709fd7188ec72334d8074b3b044fb))


### Bug Fixes

* **claude-review:** preserve recent intermediates during gc ([#198](https://github.com/otto-nation/otto-workbench/issues/198)) ([9eabcc2](https://github.com/otto-nation/otto-workbench/commit/9eabcc23cbeb574406f6c00b7a1ac188a5c7020e))

## [1.10.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.9.1...claude-review-v1.10.0) (2026-06-09)


### Features

* **commands:** add SSOT commands framework ([#196](https://github.com/otto-nation/otto-workbench/issues/196)) ([e397a38](https://github.com/otto-nation/otto-workbench/commit/e397a38b8bfed1285ee806a1c369f2b033cfbb96))

## [1.9.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.9.0...claude-review-v1.9.1) (2026-06-09)


### Bug Fixes

* **claude-review:** prompt budget logging, group diff budget, scoped file budget ([#194](https://github.com/otto-nation/otto-workbench/issues/194)) ([e6b4fd7](https://github.com/otto-nation/otto-workbench/commit/e6b4fd7e35cf61d68749f60df864cfd7935047e9))

## [1.9.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.8.0...claude-review-v1.9.0) (2026-06-08)


### Features

* **claude-review:** folder storage, smart recovery, gc ([#192](https://github.com/otto-nation/otto-workbench/issues/192)) ([849f543](https://github.com/otto-nation/otto-workbench/commit/849f543bf3695fd3fcb13adc95bc76608d907b46))

## [1.8.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.7.1...claude-review-v1.8.0) (2026-06-08)


### Features

* **claude:** manage additionalDirectories; close permission gaps ([#191](https://github.com/otto-nation/otto-workbench/issues/191)) ([88e6493](https://github.com/otto-nation/otto-workbench/commit/88e649336e820f415d0e50d64802b09dd7a81595))


### Bug Fixes

* **review:** improve review-post resilience for SHA drift and path-less findings ([#188](https://github.com/otto-nation/otto-workbench/issues/188)) ([50563d2](https://github.com/otto-nation/otto-workbench/commit/50563d262f1313dab55077c9f2ae62a033927706))
* **review:** improve synthesis resilience; eliminate permission denials ([#189](https://github.com/otto-nation/otto-workbench/issues/189)) ([151df4f](https://github.com/otto-nation/otto-workbench/commit/151df4fd15cae380e013d29776be42985ab18717))

## [1.7.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.7.0...claude-review-v1.7.1) (2026-06-08)


### Bug Fixes

* **ci:** add claude-config-release dispatch to homelab ([#186](https://github.com/otto-nation/otto-workbench/issues/186)) ([3a500e0](https://github.com/otto-nation/otto-workbench/commit/3a500e0b6748d4cd45ca9a4d2ca0d57a7a8c283e))

## [1.7.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.6.5...claude-review-v1.7.0) (2026-06-08)


### Features

* **dream:** add dream-scan and dream-verify scripts ([#184](https://github.com/otto-nation/otto-workbench/issues/184)) ([13cf944](https://github.com/otto-nation/otto-workbench/commit/13cf944c5ae0c2fb5d582e9836706c89693e07bb))
* **promote:** add promote-scan script ([#185](https://github.com/otto-nation/otto-workbench/issues/185)) ([4d7659a](https://github.com/otto-nation/otto-workbench/commit/4d7659a501babbee251339da5fa5e18bd17b595c))


### Bug Fixes

* **review:** improve orchestrate resilience for model errors and denied writes ([#183](https://github.com/otto-nation/otto-workbench/issues/183)) ([e4ae310](https://github.com/otto-nation/otto-workbench/commit/e4ae3105631969fdcd2196e1c4fc579980057b33))
* **review:** support REPO_DIR env var for cross-repo usage ([#180](https://github.com/otto-nation/otto-workbench/issues/180)) ([1a3bcdb](https://github.com/otto-nation/otto-workbench/commit/1a3bcdb5e6473af6d603ad285a151ac02a9f6b97))

## [1.6.5](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.6.4...claude-review-v1.6.5) (2026-06-05)


### Bug Fixes

* **review:** clean empty markers and fix stale verdict counts ([#178](https://github.com/otto-nation/otto-workbench/issues/178)) ([0b74247](https://github.com/otto-nation/otto-workbench/commit/0b7424749d3bd258965fcfaca0e3dd4687f7ded7))

## [1.6.4](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.6.3...claude-review-v1.6.4) (2026-06-05)


### Bug Fixes

* **review:** add git-native worktree fallback for self-review branch switch ([#176](https://github.com/otto-nation/otto-workbench/issues/176)) ([74e197e](https://github.com/otto-nation/otto-workbench/commit/74e197eda0ca54e802cb9b3f1d7a0982e7fa18c0))

## [1.6.3](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.6.2...claude-review-v1.6.3) (2026-06-05)


### Bug Fixes

* **review:** use --bare for agent invocations to skip plugins and hooks ([#174](https://github.com/otto-nation/otto-workbench/issues/174)) ([c49d495](https://github.com/otto-nation/otto-workbench/commit/c49d495bf3a421877970988519e498d52a8cebeb))

## [1.6.2](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.6.1...claude-review-v1.6.2) (2026-06-05)


### Bug Fixes

* **ci:** update build-claude-review-tarball to source lib/output.sh ([#172](https://github.com/otto-nation/otto-workbench/issues/172)) ([3a47e9f](https://github.com/otto-nation/otto-workbench/commit/3a47e9f5dbdc1b12e1b8e8217369813e4c927dfe))

## [1.6.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.6.0...claude-review-v1.6.1) (2026-06-05)


### Bug Fixes

* **review:** emit review_content in JSON summary; check agent exit codes ([#169](https://github.com/otto-nation/otto-workbench/issues/169)) ([c9bb122](https://github.com/otto-nation/otto-workbench/commit/c9bb1226fe6a3335f5660bccecfc47e87e3b70eb))

## [1.6.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.5.4...claude-review-v1.6.0) (2026-06-04)


### Features

* **review:** add evidence verification, stable IDs, and posted comment dedup ([#166](https://github.com/otto-nation/otto-workbench/issues/166)) ([003e97a](https://github.com/otto-nation/otto-workbench/commit/003e97aa4ab9b2ea99e3d7315ccd23ec83f71e5e))

## [1.5.4](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.5.3...claude-review-v1.5.4) (2026-06-04)


### Bug Fixes

* **claude-review:** support --repo-dir with --self mode ([#164](https://github.com/otto-nation/otto-workbench/issues/164)) ([8ccacde](https://github.com/otto-nation/otto-workbench/commit/8ccacde2c27e71ec004fae309a8bd8ee23be326f))

## [1.5.3](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.5.2...claude-review-v1.5.3) (2026-06-03)


### Bug Fixes

* **claude-review:** add fix quality guidelines to reviewer agent ([#160](https://github.com/otto-nation/otto-workbench/issues/160)) ([cb3a9c4](https://github.com/otto-nation/otto-workbench/commit/cb3a9c4620874441f0921a13619065f29cb3aa3b))

## [1.5.2](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.5.1...claude-review-v1.5.2) (2026-06-03)


### Bug Fixes

* **claude-review:** auto-resume failed groups; fix diagnostics ([#159](https://github.com/otto-nation/otto-workbench/issues/159)) ([377a19d](https://github.com/otto-nation/otto-workbench/commit/377a19dd1fc8e171b007d714814527948ccb3003))
* **claude-review:** truncate diff for holistic/synthesis; fix dedup and formatting ([#157](https://github.com/otto-nation/otto-workbench/issues/157)) ([e45ca4b](https://github.com/otto-nation/otto-workbench/commit/e45ca4b2372151b9b893b5a2b0da7fbcea706d6b))

## [1.5.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.5.0...claude-review-v1.5.1) (2026-06-01)


### Bug Fixes

* **claude-review:** add factual verification step to synthesis templates ([#155](https://github.com/otto-nation/otto-workbench/issues/155)) ([5290763](https://github.com/otto-nation/otto-workbench/commit/5290763a5cef6335935e8abedda8c70444ddbbfa))

## [1.5.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.4.0...claude-review-v1.5.0) (2026-05-31)


### Features

* **ai:** add config export with profile-based filtering ([#151](https://github.com/otto-nation/otto-workbench/issues/151)) ([f827a16](https://github.com/otto-nation/otto-workbench/commit/f827a16a4ea06c70f666b075247de4259308d1a1))


### Bug Fixes

* **claude-review:** add verification patterns to reduce false positives ([#154](https://github.com/otto-nation/otto-workbench/issues/154)) ([fc52f5b](https://github.com/otto-nation/otto-workbench/commit/fc52f5b5605ee52372d2cbecc082cb8d96f17ce7))

## [1.4.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.3.2...claude-review-v1.4.0) (2026-05-28)


### Features

* **claude-review:** dual-ref permalink resolution; consolidate GitHub API calls ([#147](https://github.com/otto-nation/otto-workbench/issues/147)) ([62e90dd](https://github.com/otto-nation/otto-workbench/commit/62e90ddea09581f5b714b8cedd6ff1850e7ec534))


### Bug Fixes

* **claude-review:** handle shallow clones in review pipeline ([#144](https://github.com/otto-nation/otto-workbench/issues/144)) ([2717fdd](https://github.com/otto-nation/otto-workbench/commit/2717fdd65d7304b8be092ebdd3b90d16b9b26c5d))
* **claude-review:** handle shallow clones; add metrics to JSON summary ([#146](https://github.com/otto-nation/otto-workbench/issues/146)) ([8585249](https://github.com/otto-nation/otto-workbench/commit/85852497a10e2843d875a9eb6faa3176df7462b6))

## [1.3.2](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.3.1...claude-review-v1.3.2) (2026-05-28)


### Bug Fixes

* **claude-review:** clean stale fallback worktrees before creating new ones ([#142](https://github.com/otto-nation/otto-workbench/issues/142)) ([ecbc687](https://github.com/otto-nation/otto-workbench/commit/ecbc6873680aa2ede5a04e6f8353dd5774b00437))

## [1.3.1](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.3.0...claude-review-v1.3.1) (2026-05-28)


### Bug Fixes

* **claude-review:** disable skills during review to prevent context overflow ([#140](https://github.com/otto-nation/otto-workbench/issues/140)) ([0cabf46](https://github.com/otto-nation/otto-workbench/commit/0cabf46fc91057a830f9030fe9e454e571033984))

## [1.3.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.2.0...claude-review-v1.3.0) (2026-05-27)


### Features

* **claude-review:** add --repo-dir flag for explicit repo path ([#137](https://github.com/otto-nation/otto-workbench/issues/137)) ([089cf46](https://github.com/otto-nation/otto-workbench/commit/089cf46db4575ae0c610fe7d858cff5e46eb2220))

## [1.2.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.1.0...claude-review-v1.2.0) (2026-05-26)


### Features

* **claude-review:** add --json-summary flag for structured output ([#132](https://github.com/otto-nation/otto-workbench/issues/132)) ([5008079](https://github.com/otto-nation/otto-workbench/commit/5008079e20c7e38f695727bd7d8705d8add5a985))
* **registries:** derive Claude permissions from registry allow field ([#129](https://github.com/otto-nation/otto-workbench/issues/129)) ([e35c059](https://github.com/otto-nation/otto-workbench/commit/e35c05965b7c552c49413a087b82e5b80d387034))


### Bug Fixes

* **review-post:** validate end_line against diff hunks for multi-line comments ([#131](https://github.com/otto-nation/otto-workbench/issues/131)) ([96c3862](https://github.com/otto-nation/otto-workbench/commit/96c38625cfa0f07d3d89ee83aaef1bfe22ec025f))

## [1.1.0](https://github.com/otto-nation/otto-workbench/compare/claude-review-v1.0.0...claude-review-v1.1.0) (2026-05-26)


### Features

* add component registry, docker runtime selection, MCP manifests, and tooling improvements ([#12](https://github.com/otto-nation/otto-workbench/issues/12)) ([7297a13](https://github.com/otto-nation/otto-workbench/commit/7297a13aa82b830f572a567728f2b77309b09794))
* add post-install summaries and select_menu for component prompts ([#14](https://github.com/otto-nation/otto-workbench/issues/14)) ([42002c5](https://github.com/otto-nation/otto-workbench/commit/42002c58e631389e3784f5adabf7e0f263e6d243))
* add tool context registry, validation, and auto-generation ([#15](https://github.com/otto-nation/otto-workbench/issues/15)) ([7b724e5](https://github.com/otto-nation/otto-workbench/commit/7b724e5ca30e3f5f4af9e013ea5a1c41d29b1298))
* add user override layer; improve claude-review workflow ([#46](https://github.com/otto-nation/otto-workbench/issues/46)) ([fb024b8](https://github.com/otto-nation/otto-workbench/commit/fb024b863dc40c2fd696208a3736acbefe13f184))
* add wt-cleanup script; extract docs; simplify shell control flow ([#49](https://github.com/otto-nation/otto-workbench/issues/49)) ([815414a](https://github.com/otto-nation/otto-workbench/commit/815414abf6ce220f4b81dc2131697f7fa0d60e12))
* **ai:** add agents, serena-mcp script; prune redundant rules ([#34](https://github.com/otto-nation/otto-workbench/issues/34)) ([d60d22f](https://github.com/otto-nation/otto-workbench/commit/d60d22f4a466c20580076fe2f3b34fb625028085))
* **ai:** add Claude agents, dream skill, and hook syncing ([#33](https://github.com/otto-nation/otto-workbench/issues/33)) ([6deddfa](https://github.com/otto-nation/otto-workbench/commit/6deddfa68019133f29406463ccee287ff7341671))
* **ai:** add claude-review workflow; split tool context by loading mode ([#42](https://github.com/otto-nation/otto-workbench/issues/42)) ([24cb899](https://github.com/otto-nation/otto-workbench/commit/24cb899338210441ba417b3880bf0d2b2dfc4974))
* **ai:** add coding guidelines, rule templates, init/rules bins, and workbench sync ([#13](https://github.com/otto-nation/otto-workbench/issues/13)) ([4bb2827](https://github.com/otto-nation/otto-workbench/commit/4bb2827112d693da90f7adcea0c2eba6b6432b4f))
* **ai:** add pr-review and analyze-project skills; generate public docs ([#38](https://github.com/otto-nation/otto-workbench/issues/38)) ([444e2f7](https://github.com/otto-nation/otto-workbench/commit/444e2f70dee9b6c6e79e25e7ca5a035bb9b566cb))
* **ai:** add second brain, memory backup, promote skill; harden CI and tooling ([#37](https://github.com/otto-nation/otto-workbench/issues/37)) ([0bfadd8](https://github.com/otto-nation/otto-workbench/commit/0bfadd896a6f4a5c52d428a133d37f16c8c5a780))
* **ai:** add setup script for Claude and Kiro tool configuration ([734de74](https://github.com/otto-nation/otto-workbench/commit/734de74d292edc5d2bfb5ba852c123da90da5a6b))
* **ai:** require source references in reviews; log local rule warnings ([#39](https://github.com/otto-nation/otto-workbench/issues/39)) ([1609fcc](https://github.com/otto-nation/otto-workbench/commit/1609fcc9bc8a4f3dc16a48db8cfe766c73395d35))
* **ai:** sync Claude settings, add MCPs, and skip already-installed items ([#11](https://github.com/otto-nation/otto-workbench/issues/11)) ([bba9fe8](https://github.com/otto-nation/otto-workbench/commit/bba9fe81cac5d56610477e7f8549820597061a76))
* **bin:** add gcloud-reauth script; claude-review usage stats ([#70](https://github.com/otto-nation/otto-workbench/issues/70)) ([651b058](https://github.com/otto-nation/otto-workbench/commit/651b058d10688fc63b90f3d5aa2364f9927ef57a))
* **brew:** add autoupdate tap; move review output to ~/.claude/reviews ([#41](https://github.com/otto-nation/otto-workbench/issues/41)) ([025d3c8](https://github.com/otto-nation/otto-workbench/commit/025d3c8bb8470a40eac76dade413678093326505))
* **claude-review:** add --resume flag; add validate-errexit lint ([#107](https://github.com/otto-nation/otto-workbench/issues/107)) ([69b8690](https://github.com/otto-nation/otto-workbench/commit/69b86909cef1f657537bf1df03baf2a88e9317a5))
* **claude-review:** add --resume to resume failed multi-phase reviews ([#106](https://github.com/otto-nation/otto-workbench/issues/106)) ([a068d06](https://github.com/otto-nation/otto-workbench/commit/a068d06b48910a508cb9e52292c65bde03e1c3ec))
* **claude-review:** add independent versioning and Homebrew formula ([#126](https://github.com/otto-nation/otto-workbench/issues/126)) ([f86f1c8](https://github.com/otto-nation/otto-workbench/commit/f86f1c8f680177e1358d7ba81fd16035251e4605))
* **claude-review:** add language idioms analysis phase ([#85](https://github.com/otto-nation/otto-workbench/issues/85)) ([8023c3f](https://github.com/otto-nation/otto-workbench/commit/8023c3f411e9c79405340a76375aaf95e89ab9a3))
* **claude-review:** add multi-phase parallel review for large PRs ([#69](https://github.com/otto-nation/otto-workbench/issues/69)) ([1540408](https://github.com/otto-nation/otto-workbench/commit/15404085c98d9e59bedd4477db827f65a892aaf2))
* **claude-review:** add preflight data collection to review agents ([#88](https://github.com/otto-nation/otto-workbench/issues/88)) ([8ee7bbd](https://github.com/otto-nation/otto-workbench/commit/8ee7bbde7cbba04dcf3fa510d243491a4801b3a1))
* **claude-review:** add self-review mode for pre-PR code review ([#71](https://github.com/otto-nation/otto-workbench/issues/71)) ([8177b90](https://github.com/otto-nation/otto-workbench/commit/8177b90d08b7d5279b1d3fa8025813174623bd77))
* **claude-review:** pre-flight checks; refactor(cli): noun-first ai syntax ([#80](https://github.com/otto-nation/otto-workbench/issues/80)) ([2516880](https://github.com/otto-nation/otto-workbench/commit/251688065e9e89cc3fd29aa2f6bfc935a1b8be1c))
* **registries:** add reverse bindir validation; register new tools ([#74](https://github.com/otto-nation/otto-workbench/issues/74)) ([4b17997](https://github.com/otto-nation/otto-workbench/commit/4b17997f300282283c02a278ef8c10322e2ab711))
* **review-post:** migrate tests to pytest; add API layer coverage ([#118](https://github.com/otto-nation/otto-workbench/issues/118)) ([93a155d](https://github.com/otto-nation/otto-workbench/commit/93a155dca8d9ba0a8eaaf17da9ebe65249ee9f7b))
* **reviewer:** add test quality and convention evaluation criteria ([#119](https://github.com/otto-nation/otto-workbench/issues/119)) ([45830d0](https://github.com/otto-nation/otto-workbench/commit/45830d0894b3a7defb0a3cfbf27b1b1bd4fc641c))
* **rules:** add insights-driven rules; allow /tmp writes ([#103](https://github.com/otto-nation/otto-workbench/issues/103)) ([9b272ff](https://github.com/otto-nation/otto-workbench/commit/9b272ff2c4ded1bdf9e7349f8d94d3cc7cdbf191))
* **security:** add gitleaks scanning; extract git setup ([#19](https://github.com/otto-nation/otto-workbench/issues/19)) ([bfcd53d](https://github.com/otto-nation/otto-workbench/commit/bfcd53d54186b8eb5d86e4b534b3eae5bf70f7cf))
* **state:** add component installation state tracking ([#43](https://github.com/otto-nation/otto-workbench/issues/43)) ([a8b6f7a](https://github.com/otto-nation/otto-workbench/commit/a8b6f7a57f245365620e57b1dd884f0d2e599260))
* targeted install, worktrunk migration; improve review workflow ([#40](https://github.com/otto-nation/otto-workbench/issues/40)) ([2376694](https://github.com/otto-nation/otto-workbench/commit/23766940bca66dba159df4499085de3ca8617574))
* **terminals:** consolidate terminal config, add secret model bootstrap ([#26](https://github.com/otto-nation/otto-workbench/issues/26)) ([3f0c944](https://github.com/otto-nation/otto-workbench/commit/3f0c944f189890b37546af554a4cb73f223b2f52))
* **ui:** add install_file and copy_dir; replace symlinks with copies ([#28](https://github.com/otto-nation/otto-workbench/issues/28)) ([8991b32](https://github.com/otto-nation/otto-workbench/commit/8991b322330a559e6a8cee772788f9b288eb5a44))
* **validate-nesting:** extend nesting depth validator to all languages ([#108](https://github.com/otto-nation/otto-workbench/issues/108)) ([4565cf1](https://github.com/otto-nation/otto-workbench/commit/4565cf1a5e1286058f771ffe2eff7cb084eda877))
* workbench improvements — warnings, worktrees, component scripts, cleanup ([#36](https://github.com/otto-nation/otto-workbench/issues/36)) ([d357403](https://github.com/otto-nation/otto-workbench/commit/d357403221001ca8f4043636f62e6d62ff2b96b3))


### Bug Fixes

* **claude-review:** conditional preflight packing; ERR trap; set -e function pitfall ([#104](https://github.com/otto-nation/otto-workbench/issues/104)) ([9f4196e](https://github.com/otto-nation/otto-workbench/commit/9f4196ee301010f07eeaaf6803cb4fdcf604ef5f))
* **claude-review:** drop subject_type from inline comments ([#115](https://github.com/otto-nation/otto-workbench/issues/115)) ([64a792f](https://github.com/otto-nation/otto-workbench/commit/64a792ff37428f365cb66bb7569742364e758bb4))
* **claude-review:** fix review posting; reduce synthesis context ([#114](https://github.com/otto-nation/otto-workbench/issues/114)) ([c4a8e51](https://github.com/otto-nation/otto-workbench/commit/c4a8e51ebe2e582b26bb7a966147f9f5c1b41bef))
* **claude-review:** handle Ctrl+C gracefully across all scripts ([#122](https://github.com/otto-nation/otto-workbench/issues/122)) ([ba96585](https://github.com/otto-nation/otto-workbench/commit/ba96585425e299643eb28a2dc00f830dd70f1a48))
* **claude-review:** move self-review out of sensitive .claude/ dir ([#109](https://github.com/otto-nation/otto-workbench/issues/109)) ([8052151](https://github.com/otto-nation/otto-workbench/commit/8052151451c0d38bcd1ac89abadcb8304e696b8b))
* **claude-review:** self-review archive, --force, and --no-post rule ([#100](https://github.com/otto-nation/otto-workbench/issues/100)) ([eeac16a](https://github.com/otto-nation/otto-workbench/commit/eeac16aa08dd38a9fa0747e5a3da88978688b597))
* **claude-review:** use explicit prompt and skill file for post command ([#66](https://github.com/otto-nation/otto-workbench/issues/66)) ([792817d](https://github.com/otto-nation/otto-workbench/commit/792817d1168e434de4ed2fa46bed55c915d7bbb8))
* enforce PR template usage via rule and hook ([#84](https://github.com/otto-nation/otto-workbench/issues/84)) ([ce9c45f](https://github.com/otto-nation/otto-workbench/commit/ce9c45f436c96fe9e5f6eb372279b0d2d34e127d))
* **git:** worktree hook delegation; refactor claude-review with poster agent ([#45](https://github.com/otto-nation/otto-workbench/issues/45)) ([c9c6126](https://github.com/otto-nation/otto-workbench/commit/c9c612653d82a90ed30b64416136e1704bbf52fc))
* **review-orchestrate:** include uncommitted changes in self-review metadata ([#120](https://github.com/otto-nation/otto-workbench/issues/120)) ([51989ca](https://github.com/otto-nation/otto-workbench/commit/51989caccb8b96459c01313ac52eb3044b13687c))
* **review-post:** chunk large reviews; improve rate limit retry ([#117](https://github.com/otto-nation/otto-workbench/issues/117)) ([be85ce8](https://github.com/otto-nation/otto-workbench/commit/be85ce8842fcf57fa49b7fd553a176add6b001d5))
* **review-post:** derive default severity filter from SEVERITY_LABELS ([#94](https://github.com/otto-nation/otto-workbench/issues/94)) ([37f0db6](https://github.com/otto-nation/otto-workbench/commit/37f0db61c077d3850a69d81156a8478e8f4776f5))
* **review-post:** fallback to body-level when inline lines can't be resolved ([#116](https://github.com/otto-nation/otto-workbench/issues/116)) ([8c311c0](https://github.com/otto-nation/otto-workbench/commit/8c311c082fba8f0feea477be9429372637af5273))
* **review-post:** prevent double-finalization from dropping finding body text ([#86](https://github.com/otto-nation/otto-workbench/issues/86)) ([574aa51](https://github.com/otto-nation/otto-workbench/commit/574aa5148c8ee63fb537755558dc18b914d668e1))
* **review-post:** validate end_line against diff hunks for multi-line comments ([#121](https://github.com/otto-nation/otto-workbench/issues/121)) ([d02ad30](https://github.com/otto-nation/otto-workbench/commit/d02ad30556c9f3389b1f52aa7b9454b019443765))
* **review:** grant write access to review file's parent directory ([#92](https://github.com/otto-nation/otto-workbench/issues/92)) ([1450e2b](https://github.com/otto-nation/otto-workbench/commit/1450e2bbc4e7922abc783a00c60ff78be905171b))
* **skills:** escape PR reply bodies with heredoc pipe ([#110](https://github.com/otto-nation/otto-workbench/issues/110)) ([d2ac529](https://github.com/otto-nation/otto-workbench/commit/d2ac5294151f2d545ea0df1c38eea42d28411069))
* **zed:** use python3 JSONC parser; add brew fpath before compinit ([#30](https://github.com/otto-nation/otto-workbench/issues/30)) ([7fcd622](https://github.com/otto-nation/otto-workbench/commit/7fcd622d6286ad29138d47358246b39e24df30cb))


### Performance Improvements

* **claude-review:** budget controls, scoped diffs; reduce review cost ([#98](https://github.com/otto-nation/otto-workbench/issues/98)) ([4f09035](https://github.com/otto-nation/otto-workbench/commit/4f090352c340725e4a5a2fa857edd1e0b0f5b63e))
* **claude-review:** optimize review pipeline and add metadata tracking ([#95](https://github.com/otto-nation/otto-workbench/issues/95)) ([8ea407a](https://github.com/otto-nation/otto-workbench/commit/8ea407a1fe873d3570a3e99733954580d6d173f8))


### Code Refactoring

* add context field to registries; clean up stale references ([#68](https://github.com/otto-nation/otto-workbench/issues/68)) ([0a52e1d](https://github.com/otto-nation/otto-workbench/commit/0a52e1d2090134989a805e37aa395f499d55c660))
* **ai:** modularize AI lib, harden scripts, add component validation ([#25](https://github.com/otto-nation/otto-workbench/issues/25)) ([dbf7b8c](https://github.com/otto-nation/otto-workbench/commit/dbf7b8ccfbe206e5ff02f5f72bafe8daa802f99d))
* **brew:** reorganize optional Brewfiles into category subdirs ([#24](https://github.com/otto-nation/otto-workbench/issues/24)) ([db269c8](https://github.com/otto-nation/otto-workbench/commit/db269c8694e613362da90dea9c7682f6fb5b7fdf))
* centralize output helpers; move usage text to usage() ([#72](https://github.com/otto-nation/otto-workbench/issues/72)) ([a73fba6](https://github.com/otto-nation/otto-workbench/commit/a73fba6f83d395dcdd1ca2691cf8768bdbee0394))
* **claude-review:** extract post logic into review-post ([#83](https://github.com/otto-nation/otto-workbench/issues/83)) ([5f58538](https://github.com/otto-nation/otto-workbench/commit/5f58538612749f996348824ef276fad0190947d1))
* **claude:** replace poster agent with /pr-review skill ([#63](https://github.com/otto-nation/otto-workbench/issues/63)) ([42a6b69](https://github.com/otto-nation/otto-workbench/commit/42a6b698a477bcf1ef87f5893727ae9470792bd1))
* **cli:** move claude and override commands under ai subcommand ([#75](https://github.com/otto-nation/otto-workbench/issues/75)) ([c7f7e4c](https://github.com/otto-nation/otto-workbench/commit/c7f7e4c7011797b5bb8f61b6cef683a157d44d21))
* **lib:** centralize constants, expand docs and test setup ([#16](https://github.com/otto-nation/otto-workbench/issues/16)) ([f28a167](https://github.com/otto-nation/otto-workbench/commit/f28a167e02c13ca90b0c3a2a6d0ada06c174b5bc))
* relocate user overrides from repo to XDG state dir ([7794730](https://github.com/otto-nation/otto-workbench/commit/77947302744edcd77826856122d60176ac461aab))
* restructure lib modules; add per-org GH_TOKEN; harden scripts ([#31](https://github.com/otto-nation/otto-workbench/issues/31)) ([b757b32](https://github.com/otto-nation/otto-workbench/commit/b757b32e34f78fa94bb6bb56f02c9d2900573f6b))
* **workbench:** centralize paths, modularize steps, auto-discover components ([#23](https://github.com/otto-nation/otto-workbench/issues/23)) ([bf61b3b](https://github.com/otto-nation/otto-workbench/commit/bf61b3bb82783d238c17685749816c2854df27d4))
* **workbench:** reorganize scripts, env management; add nesting validator and GPG setup ([#48](https://github.com/otto-nation/otto-workbench/issues/48)) ([fff0b20](https://github.com/otto-nation/otto-workbench/commit/fff0b20c64a1596a992e61d8e56920e255137432))

## Changelog
