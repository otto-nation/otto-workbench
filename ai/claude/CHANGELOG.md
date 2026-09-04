# Changelog

## [2.0.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.42.0...otto-ai-tools-v2.0.0) (2026-09-04)


### ⚠ BREAKING CHANGES

* **ai:** the claude-rules command is renamed workbench-rules; it no longer writes into ~/.claude/rules/, and its local rules now live under the workbench config root's override tree
* **ai:** the review.model, review.thinking, review.provider and review.phases config keys now live under agent.*, and the CLAUDE_REVIEW_MODEL / CLAUDE_REVIEW_THINKING / CLAUDE_REVIEW_<PHASE>_* environment keys are replaced by the WORKBENCH_AI_* keys. A machine's config.yml is rewritten by the 20260824-agent-config-section migration; a project's own .workbench.yml and any exported environment need updating by hand.

### Features

* **ai:** add usage ledger; extend the eval harness and lint gates ([#603](https://github.com/otto-nation/otto-workbench/issues/603)) ([64a68c8](https://github.com/otto-nation/otto-workbench/commit/64a68c81a5458762fac99fc678fc4d57bca11aec))
* **ai:** carry the workbench's guidelines and agent protocols to Pi ([#1116](https://github.com/otto-nation/otto-workbench/issues/1116)) ([b05a35c](https://github.com/otto-nation/otto-workbench/commit/b05a35cda29e22488d376ee0ce9d8a2a6a095dff))
* **ai:** install one skills tree into both Claude Code and Pi ([#1101](https://github.com/otto-nation/otto-workbench/issues/1101)) ([fb09321](https://github.com/otto-nation/otto-workbench/commit/fb0932199ad694748059817fd06c09060b675ead))
* **ai:** one owner for committing a pass's work and pushing it ([#984](https://github.com/otto-nation/otto-workbench/issues/984)) ([f8b3131](https://github.com/otto-nation/otto-workbench/commit/f8b31317a684438746aab62ca93a82e9c580dd1e)), closes [#904](https://github.com/otto-nation/otto-workbench/issues/904)
* **ai:** record a thread the operator settled by hand ([#1009](https://github.com/otto-nation/otto-workbench/issues/1009)) ([02b5099](https://github.com/otto-nation/otto-workbench/commit/02b50992bbaff469b48e6e4e69ff689fec03ee22))
* **ai:** route every prompt-shaped agent call through the one owner ([#993](https://github.com/otto-nation/otto-workbench/issues/993)) ([37eff93](https://github.com/otto-nation/otto-workbench/commit/37eff9394337e7e781ed10ca287f2d9dfb563553))
* **claude:** suppress Claude commit/PR attribution via settings ([#1130](https://github.com/otto-nation/otto-workbench/issues/1130)) ([bbe7c56](https://github.com/otto-nation/otto-workbench/commit/bbe7c567d009758636be302b2d3c72f71177ce99))
* **claude:** trust the node family; block four more prompt shapes ([#739](https://github.com/otto-nation/otto-workbench/issues/739)) ([f045dea](https://github.com/otto-nation/otto-workbench/commit/f045deab920a458308b414673a838983d348a323))
* **config:** learn each repo's issue tracker instead of assuming ([#828](https://github.com/otto-nation/otto-workbench/issues/828)) ([fc34cfc](https://github.com/otto-nation/otto-workbench/commit/fc34cfcd51c78926d88e9d0f349145e3764fde7e))
* **config:** report every scope and read the container's .workbench.yml ([#983](https://github.com/otto-nation/otto-workbench/issues/983)) ([c8dc56a](https://github.com/otto-nation/otto-workbench/commit/c8dc56a74689adee4a3df5e15d692ef6eca71e52))
* **eval:** add a fixture harness for in-session skill execution ([#687](https://github.com/otto-nation/otto-workbench/issues/687)) ([e74d949](https://github.com/otto-nation/otto-workbench/commit/e74d949497bac7f8d96a242c3795016399a9fd9d))
* **eval:** add history tracking, comparison, and CI gate ([#574](https://github.com/otto-nation/otto-workbench/issues/574)) ([94bf699](https://github.com/otto-nation/otto-workbench/commit/94bf699cab9177a3d7caf018a58c2451ec50525f))
* **git:** verify a push actually landed on the remote ([#972](https://github.com/otto-nation/otto-workbench/issues/972)) ([b95f8ec](https://github.com/otto-nation/otto-workbench/commit/b95f8ec548bd3214f73ba530772c272745cfcb27))
* **git:** verify hand-typed pushes via pre-push intent recording ([#987](https://github.com/otto-nation/otto-workbench/issues/987)) ([c69866f](https://github.com/otto-nation/otto-workbench/commit/c69866f8be79d778775760414aca916487075971))
* **machine:** name each registered repo's issue tracker in the profile ([#923](https://github.com/otto-nation/otto-workbench/issues/923)) ([17955a1](https://github.com/otto-nation/otto-workbench/commit/17955a117e0aa6701fc39f32506be1d0b666d787))
* **mcp:** re-discover tools without a client restart ([#887](https://github.com/otto-nation/otto-workbench/issues/887)) ([81555f7](https://github.com/otto-nation/otto-workbench/commit/81555f7bef40288e74ad295742564d8c9bfc3ab5))
* **migrations:** run a migration once per repo, not per worktree ([#1049](https://github.com/otto-nation/otto-workbench/issues/1049)) ([8a1060a](https://github.com/otto-nation/otto-workbench/commit/8a1060a38cae584b155ef651ff174444cbb067c7))
* **permissions:** mirror tracked grants into the bare-repo container ([#946](https://github.com/otto-nation/otto-workbench/issues/946)) ([e2c581d](https://github.com/otto-nation/otto-workbench/commit/e2c581d608f96245e3c373f49a76abe8209f1ac4))
* **pi:** install Pi with its own installer during AI setup ([#1094](https://github.com/otto-nation/otto-workbench/issues/1094)) ([a629b2e](https://github.com/otto-nation/otto-workbench/commit/a629b2e8e4eab25d719640c703112fad46b08d99))
* **pr-comments:** scope summary comments to the round's new activity ([#931](https://github.com/otto-nation/otto-workbench/issues/931)) ([b5660fc](https://github.com/otto-nation/otto-workbench/commit/b5660fcf1dfb528a6539004975cc2cc9a7325caf))
* **pr-rebase:** add lockfile regeneration for all ecosystems ([#593](https://github.com/otto-nation/otto-workbench/issues/593)) ([1f6d14b](https://github.com/otto-nation/otto-workbench/commit/1f6d14b4d53e7d05e1b93092065060dff0664046))
* **pr-review:** resolve model aliases; gate runs on backend preflight ([#588](https://github.com/otto-nation/otto-workbench/issues/588)) ([98a90cf](https://github.com/otto-nation/otto-workbench/commit/98a90cf53ad9a493ef33e52dc5342d303f80fe29))
* **pr:** gate PR creation on nesting depth ratchet ([#587](https://github.com/otto-nation/otto-workbench/issues/587)) ([7e86c77](https://github.com/otto-nation/otto-workbench/commit/7e86c77a5396a30b046e857c140c80705b9031b7))
* **pr:** guard worktree_root and lint for unguarded dereferences ([#617](https://github.com/otto-nation/otto-workbench/issues/617)) ([a3f54d9](https://github.com/otto-nation/otto-workbench/commit/a3f54d98b79e8db4a1bbc38673e06e2bf9fb0c3d))
* **projects:** group a repo's worktrees under the repo ([#1061](https://github.com/otto-nation/otto-workbench/issues/1061)) ([6dec134](https://github.com/otto-nation/otto-workbench/commit/6dec1349f551d1375e45e0691847dc405f96abd5))
* **projects:** record the repos that use the workbench ([#807](https://github.com/otto-nation/otto-workbench/issues/807)) ([b1a44ec](https://github.com/otto-nation/otto-workbench/commit/b1a44ec1ce50e2b21d6c944de5b64db2e8c3ae62))
* **pr:** publish reviews through the CLI, not path fixtures ([#873](https://github.com/otto-nation/otto-workbench/issues/873)) ([6334971](https://github.com/otto-nation/otto-workbench/commit/63349713e2076d48a7aa14476357e65836561e79))
* **pr:** run the supersession preflight beyond pr comments ([#768](https://github.com/otto-nation/otto-workbench/issues/768)) ([1abb2a2](https://github.com/otto-nation/otto-workbench/commit/1abb2a2c31c0932de006e5dd04355f6b2e5365b7))
* **review:** add failure reporting, status metadata, and --recover flag ([#581](https://github.com/otto-nation/otto-workbench/issues/581)) ([9ba7b94](https://github.com/otto-nation/otto-workbench/commit/9ba7b9419a23ad8935c013a4b7b6cc0bb4aa5a46))
* **review:** add model evaluation system for review pipeline ([#572](https://github.com/otto-nation/otto-workbench/issues/572)) ([0f8c25d](https://github.com/otto-nation/otto-workbench/commit/0f8c25dc3a249a2a21ae9ba89e441e507edb6916))
* **surface:** fail a push that removes public surface undeclared ([#838](https://github.com/otto-nation/otto-workbench/issues/838)) ([8091677](https://github.com/otto-nation/otto-workbench/commit/80916778d035c0b361be6fcd991ab73076ac4d64))
* **tools:** add dynamic tool framework with MCP server and self-describing protocol ([#577](https://github.com/otto-nation/otto-workbench/issues/577)) ([53e1dd8](https://github.com/otto-nation/otto-workbench/commit/53e1dd8d6a04d4a260eb5cf21bd67cb196212697))
* **trail:** bound the trail root and exempt the listing from it ([#886](https://github.com/otto-nation/otto-workbench/issues/886)) ([04f2414](https://github.com/otto-nation/otto-workbench/commit/04f2414edc87d27a0b8b567b5096349afe3af3e1))
* **validate:** fail CI when a --tool-schema claim goes unanswered ([#800](https://github.com/otto-nation/otto-workbench/issues/800)) ([60b6396](https://github.com/otto-nation/otto-workbench/commit/60b63963f58f88410447e416a310ef5db9a93ace))
* **validate:** flag a subprocess call that names no timeout ([#919](https://github.com/otto-nation/otto-workbench/issues/919)) ([8329786](https://github.com/otto-nation/otto-workbench/commit/83297860024291bbbfa1aa9d38e4d576b5c1916d))
* **workbench:** unified config.yml with a generated JSON schema ([#726](https://github.com/otto-nation/otto-workbench/issues/726)) ([05130d3](https://github.com/otto-nation/otto-workbench/commit/05130d3f2f4c753b34c76cf57fbb5ebfe94b8b06))
* **zsh:** launch claude in a worktree, not the bare container ([#961](https://github.com/otto-nation/otto-workbench/issues/961)) ([af0f215](https://github.com/otto-nation/otto-workbench/commit/af0f215ef5acc57e818b518aec322302c56f188d))


### Bug Fixes

* **ai:** block env -C; cite rules sections in guard messages ([#688](https://github.com/otto-nation/otto-workbench/issues/688)) ([1f6ec9a](https://github.com/otto-nation/otto-workbench/commit/1f6ec9ab87fc0b69c8233ed53b45b2c6085d8fb0))
* **ai:** finish an unpushed pr-rebase run through the owner ([#998](https://github.com/otto-nation/otto-workbench/issues/998)) ([e5383b3](https://github.com/otto-nation/otto-workbench/commit/e5383b3ad7e034f9c1c9b70e83aa653c9cba1fea))
* **ai:** give the rule layers a home no harness owns ([#1133](https://github.com/otto-nation/otto-workbench/issues/1133)) ([a9b22a7](https://github.com/otto-nation/otto-workbench/commit/a9b22a72f4d718fdd964ac8c774ccddfd3a0185a))
* **ai:** install the harness-neutral CLIs for every harness ([#1127](https://github.com/otto-nation/otto-workbench/issues/1127)) ([63fc6df](https://github.com/otto-nation/otto-workbench/commit/63fc6dfeaa823e223c3e5bfad49e46a234e4842a))
* **ai:** make ~/.env.local the one owner of the Vertex routing vars ([#1120](https://github.com/otto-nation/otto-workbench/issues/1120)) ([3f25472](https://github.com/otto-nation/otto-workbench/commit/3f2547297ec3be8b09dbd9151649a1a0f0be1d83))
* **ai:** pin every AI subprocess to an explicit cwd ([#638](https://github.com/otto-nation/otto-workbench/issues/638)) ([03aff58](https://github.com/otto-nation/otto-workbench/commit/03aff58ccd533b5f85919fb6d22fd7aeeab9af3a))
* **ai:** reconcile a moved HEAD in the finish-phase fix replies ([#827](https://github.com/otto-nation/otto-workbench/issues/827)) ([a5b970c](https://github.com/otto-nation/otto-workbench/commit/a5b970c5b72413109137a0787373e89f9c444a39))
* **ai:** refuse a rebase onto a base the branch cannot safely replay onto ([#846](https://github.com/otto-nation/otto-workbench/issues/846)) ([dbe93cf](https://github.com/otto-nation/otto-workbench/commit/dbe93cf589f43c9532525329a64158411d6da0d5))
* **ai:** resolve the worktree before writing a project artifact ([#973](https://github.com/otto-nation/otto-workbench/issues/973)) ([3a8eede](https://github.com/otto-nation/otto-workbench/commit/3a8eede04baa692569c037133935ed5e4c81e8ac))
* **ai:** scope the rebase fix pass to the files a check names ([#845](https://github.com/otto-nation/otto-workbench/issues/845)) ([a23cdcc](https://github.com/otto-nation/otto-workbench/commit/a23cdcc5aefe30aa99225c295b97f1392d3e2a0c))
* **ai:** stop prescribing a shell the allowlist does not grant ([#920](https://github.com/otto-nation/otto-workbench/issues/920)) ([e5dcf00](https://github.com/otto-nation/otto-workbench/commit/e5dcf005440e662fc81304ef0ef325ad7f2e3b0f))
* **ai:** stop review-threads restating every open thread each round ([#1021](https://github.com/otto-nation/otto-workbench/issues/1021)) ([a1d6cce](https://github.com/otto-nation/otto-workbench/commit/a1d6cce8068b8ba071310ea2a3298164b695d128))
* **anatomy:** drop per-file forks; index directories past the cap ([#722](https://github.com/otto-nation/otto-workbench/issues/722)) ([64b8630](https://github.com/otto-nation/otto-workbench/commit/64b863047b3da3b85c86a5638d1362139cf410be))
* **bash-tool:** steer background processes to run_in_background ([#792](https://github.com/otto-nation/otto-workbench/issues/792)) ([94892fa](https://github.com/otto-nation/otto-workbench/commit/94892fa8d46b4cd263e7922c63beb1eeb894fb8a))
* **ceiling-scan:** skip binaries and gate the quadratic regex ([#716](https://github.com/otto-nation/otto-workbench/issues/716)) ([42b126d](https://github.com/otto-nation/otto-workbench/commit/42b126df1f6ca67814ae4d9fa8589a286709cbef))
* **ceiling:** read the trigger the marker already names ([#772](https://github.com/otto-nation/otto-workbench/issues/772)) ([fe85572](https://github.com/otto-nation/otto-workbench/commit/fe85572808716d97423816621e39ac950e79106b))
* **ci:** anchor bats failures on the line the log reported ([#1076](https://github.com/otto-nation/otto-workbench/issues/1076)) ([c43d1b2](https://github.com/otto-nation/otto-workbench/commit/c43d1b2954774f189e3795252bccebd4a1d19b5f))
* **claude:** allow node in the Bash permission list ([#690](https://github.com/otto-nation/otto-workbench/issues/690)) ([1b04f19](https://github.com/otto-nation/otto-workbench/commit/1b04f1901d46afff2ebd119ed72beb35de5cf52c))
* **claude:** block absolute paths to /bin and /usr/bin binaries ([#689](https://github.com/otto-nation/otto-workbench/issues/689)) ([3b470a9](https://github.com/otto-nation/otto-workbench/commit/3b470a9f935d9299d2f846295f42433fe6dc2348))
* **claude:** block bash function definitions in the permission guardrails ([#611](https://github.com/otto-nation/otto-workbench/issues/611)) ([3c1fde4](https://github.com/otto-nation/otto-workbench/commit/3c1fde453d5442b27c0c173f9a9c7704c4f3091d))
* **claude:** grant read access to rtk's tee logs ([#1007](https://github.com/otto-nation/otto-workbench/issues/1007)) ([55f9c4b](https://github.com/otto-nation/otto-workbench/commit/55f9c4b5bb81f2e3a0f3326a00b81b41b5f42757))
* **claude:** install Claude Code with its own installer, not the cask ([#1088](https://github.com/otto-nation/otto-workbench/issues/1088)) ([b465bc2](https://github.com/otto-nation/otto-workbench/commit/b465bc2cac5572851d303b77a7d80204aea08de0))
* **claude:** keep the settings manifest out of the validated file ([#1098](https://github.com/otto-nation/otto-workbench/issues/1098)) ([99a04a0](https://github.com/otto-nation/otto-workbench/commit/99a04a0e3ad889003f1822d9a6c778ce7c8b276a))
* **claude:** point hook script paths at ~/.local/bin ([#616](https://github.com/otto-nation/otto-workbench/issues/616)) ([751ff64](https://github.com/otto-nation/otto-workbench/commit/751ff647db304cb5eeb68fe791f43ad18e78ce40))
* **cli:** drop the --repo alias; run every validator in both gates ([#633](https://github.com/otto-nation/otto-workbench/issues/633)) ([89accd3](https://github.com/otto-nation/otto-workbench/commit/89accd34a05d893b4f672c01ec20fe918e9157e9))
* **config:** refuse a write under a key the workbench cannot read ([#965](https://github.com/otto-nation/otto-workbench/issues/965)) ([21ccf03](https://github.com/otto-nation/otto-workbench/commit/21ccf039027d9d602e2014e1956e14913b752be6))
* **git:** a failed git status no longer reads as a clean tree ([#974](https://github.com/otto-nation/otto-workbench/issues/974)) ([d895ba1](https://github.com/otto-nation/otto-workbench/commit/d895ba15786e3869a6e67d899d73d7cd56cc22a8))
* **git:** measure pre-push nesting against the pushed diff ([#1015](https://github.com/otto-nation/otto-workbench/issues/1015)) ([54d5cfd](https://github.com/otto-nation/otto-workbench/commit/54d5cfd655db74eb6a2e01c06fa7f99b898e94c7))
* **lock:** scope the run lock and PR state to the run's target ([#692](https://github.com/otto-nation/otto-workbench/issues/692)) ([9cedd92](https://github.com/otto-nation/otto-workbench/commit/9cedd926b67abead75f650b30a410632eb297100))
* **machine:** read the project registry instead of guessing ([#808](https://github.com/otto-nation/otto-workbench/issues/808)) ([777fc4b](https://github.com/otto-nation/otto-workbench/commit/777fc4b284068bdc35e08b20a1aa0bb8643348f1))
* **machine:** resolve the workbench's own location from its own path ([#829](https://github.com/otto-nation/otto-workbench/issues/829)) ([34008f1](https://github.com/otto-nation/otto-workbench/commit/34008f105d084378d81cf1a156a78273e7ef03f2))
* **mcp:** answer a client instead of failing every request ([#878](https://github.com/otto-nation/otto-workbench/issues/878)) ([b95bbae](https://github.com/otto-nation/otto-workbench/commit/b95bbaee6265829eb1de7c7a3c986cb6c281f935))
* **mcp:** derive tool discovery dirs from the component layout ([#760](https://github.com/otto-nation/otto-workbench/issues/760)) ([9527415](https://github.com/otto-nation/otto-workbench/commit/9527415579e5ccd090f4c9904f3afd9b7bd2c1d7))
* **mcp:** isolate tool subprocesses; fix false schema and collisions ([#1135](https://github.com/otto-nation/otto-workbench/issues/1135)) ([c89e82d](https://github.com/otto-nation/otto-workbench/commit/c89e82d33b8b72f9709a0e9d57bc41e47b5dad08))
* **mcp:** offer a client only the tools the registries make visible ([#884](https://github.com/otto-nation/otto-workbench/issues/884)) ([9894279](https://github.com/otto-nation/otto-workbench/commit/9894279d64af3d8a814689bb4ce94722c651b957))
* **mcp:** probe tools concurrently so a slow probe keeps its tool ([#975](https://github.com/otto-nation/otto-workbench/issues/975)) ([06e7119](https://github.com/otto-nation/otto-workbench/commit/06e7119f963ac605f358e45094b7e9a8e393ab08))
* **mcp:** stop probing executables that don't declare --tool-schema ([#592](https://github.com/otto-nation/otto-workbench/issues/592)) ([e8c1343](https://github.com/otto-nation/otto-workbench/commit/e8c134387d1779f511b999a6c348d193e5cd4234))
* **migrations:** defer a migration whose target does not exist yet ([#964](https://github.com/otto-nation/otto-workbench/issues/964)) ([1b698dc](https://github.com/otto-nation/otto-workbench/commit/1b698dc069cdb8839e1789ee8c76e08d2405d968))
* **migrations:** give adoption-sensitive migrations another pass ([#756](https://github.com/otto-nation/otto-workbench/issues/756)) ([023a079](https://github.com/otto-nation/otto-workbench/commit/023a079275995af4b00b1943257e69f98ea46e95))
* **migrations:** isolate migration failures from the sync run ([#750](https://github.com/otto-nation/otto-workbench/issues/750)) ([806f0c7](https://github.com/otto-nation/otto-workbench/commit/806f0c715fd547079acbb88bbda045472ba23a26))
* **migrations:** record a project-scoped migration once per repo ([#872](https://github.com/otto-nation/otto-workbench/issues/872)) ([b9c0dd2](https://github.com/otto-nation/otto-workbench/commit/b9c0dd2067632349142b3a2dfe08e235d2e8a421))
* **migrations:** report repos changed, not repos visited ([#925](https://github.com/otto-nation/otto-workbench/issues/925)) ([5917a8c](https://github.com/otto-nation/otto-workbench/commit/5917a8c31bfcb27a7afb20294d455b90223ef16a))
* **permissions:** revive four rules that could never match ([#785](https://github.com/otto-nation/otto-workbench/issues/785)) ([5eecf4b](https://github.com/otto-nation/otto-workbench/commit/5eecf4ba8a8b1670a4a0611b7ac8cba40df1b4d6))
* **permissions:** stop reading a nested bin/ as the granted top-level one ([#879](https://github.com/otto-nation/otto-workbench/issues/879)) ([4a43869](https://github.com/otto-nation/otto-workbench/commit/4a43869557eb9842d318e40f21b75958db067da8))
* **pr-comments:** a draft round owes every table it printed ([#1054](https://github.com/otto-nation/otto-workbench/issues/1054)) ([e1e198d](https://github.com/otto-nation/otto-workbench/commit/e1e198d11a9593b65aa3e0d0467ac54d7577a252))
* **pr-comments:** a resolved thread is not evidence of a fix ([#1081](https://github.com/otto-nation/otto-workbench/issues/1081)) ([ff3af6d](https://github.com/otto-nation/otto-workbench/commit/ff3af6d3835bb7a694c317a56925d05ff8ddae92))
* **pr-comments:** a row the fix pass did not land cites no commit ([#1056](https://github.com/otto-nation/otto-workbench/issues/1056)) ([462a734](https://github.com/otto-nation/otto-workbench/commit/462a734653928d35ffa7859cc7f004c82c56b0ba))
* **pr-comments:** deduplicate reply posts across all reply types ([#576](https://github.com/otto-nation/otto-workbench/issues/576)) ([d3af54c](https://github.com/otto-nation/otto-workbench/commit/d3af54c46c82dcbf76e1c5d06e2accfda82025b2))
* **pr-comments:** gate summary and replies on push success; defer for unaccounted threads ([#579](https://github.com/otto-nation/otto-workbench/issues/579)) ([4608f84](https://github.com/otto-nation/otto-workbench/commit/4608f84e28547255f5e41720f63548acd640e2ed))
* **pr-comments:** give the fix pass a budget it can finish within ([#642](https://github.com/otto-nation/otto-workbench/issues/642)) ([eb9bc0c](https://github.com/otto-nation/otto-workbench/commit/eb9bc0c0f382ec41a1753715c1d141f28f51cf68))
* **pr-comments:** hold the push while a thread is contested ([#718](https://github.com/otto-nation/otto-workbench/issues/718)) ([d001d13](https://github.com/otto-nation/otto-workbench/commit/d001d1326db61949834479477a2e4263b86e87c1))
* **pr-comments:** keep the summary comment complete across rounds ([#727](https://github.com/otto-nation/otto-workbench/issues/727)) ([5fa9b69](https://github.com/otto-nation/otto-workbench/commit/5fa9b6910a6f3f3052d978738e6e2ea062adecfe))
* **pr-comments:** one reply per thread, edited in place, always cited ([#610](https://github.com/otto-nation/otto-workbench/issues/610)) ([54bf408](https://github.com/otto-nation/otto-workbench/commit/54bf4081e12ede44999bdf6ace998e212dc56a60))
* **pr-comments:** pin reply line anchors to the tree they were read in ([#896](https://github.com/otto-nation/otto-workbench/issues/896)) ([708d83d](https://github.com/otto-nation/otto-workbench/commit/708d83da4aebcfa59a8b4da4b64d7d6973eac7f7))
* **pr-comments:** queue triage replies so --finish can drain a draft ([#781](https://github.com/otto-nation/otto-workbench/issues/781)) ([4c8937f](https://github.com/otto-nation/otto-workbench/commit/4c8937f7a254f05e44ed03c9176606706eec4569))
* **pr-comments:** repost the summary once a reviewer answers it ([#773](https://github.com/otto-nation/otto-workbench/issues/773)) ([8779014](https://github.com/otto-nation/otto-workbench/commit/8779014c46b5f7e8bb7049a53547371d454c1c53))
* **pr-comments:** require --track to file a deferral; attribute fixes per commit ([#643](https://github.com/otto-nation/otto-workbench/issues/643)) ([65d4bf1](https://github.com/otto-nation/otto-workbench/commit/65d4bf10ed1e23f7ef61eb2ebc8ff255f28c583f))
* **pr-comments:** resolve addressed threads; exclude from triage and summary ([#583](https://github.com/otto-nation/otto-workbench/issues/583)) ([3c19b62](https://github.com/otto-nation/otto-workbench/commit/3c19b62a1db558df26115b4c1f5988f6456246e5))
* **pr-comments:** scope a summary against its edited body, not its post time ([#1080](https://github.com/otto-nation/otto-workbench/issues/1080)) ([70ec1f0](https://github.com/otto-nation/otto-workbench/commit/70ec1f0d4517e91252bbb842f0f73e45353b1fc6))
* **pr-comments:** stop reporting a drafted --reply as a failure ([#630](https://github.com/otto-nation/otto-workbench/issues/630)) ([5472746](https://github.com/otto-nation/otto-workbench/commit/5472746f7b1d57e7b67658582685dec3147f3969))
* **pr-context:** surface command stderr instead of asserting a cause ([#748](https://github.com/otto-nation/otto-workbench/issues/748)) ([2d37dc0](https://github.com/otto-nation/otto-workbench/commit/2d37dc0e75c2b313bfee161585a0a16234c7d727))
* **pr-rebase:** --push runs rebase before pushing; add status to rebase state ([#568](https://github.com/otto-nation/otto-workbench/issues/568)) ([89a33ff](https://github.com/otto-nation/otto-workbench/commit/89a33ff9ca140ee11ab5c8b60fcc7d021a5af4de))
* **pr-rebase:** commit the hook-validated tree and protect stashed work ([#666](https://github.com/otto-nation/otto-workbench/issues/666)) ([731b08e](https://github.com/otto-nation/otto-workbench/commit/731b08efd532697358e955b51e800d35f19c5480))
* **pr-rebase:** detect .mise.toml and survive missing regen binary ([#596](https://github.com/otto-nation/otto-workbench/issues/596)) ([61ab316](https://github.com/otto-nation/otto-workbench/commit/61ab31600633a60e2ef34840ab227db7c2debf10))
* **pr-rebase:** force-push by default after clean rebase ([#573](https://github.com/otto-nation/otto-workbench/issues/573)) ([7c85164](https://github.com/otto-nation/otto-workbench/commit/7c8516427859e27be1386a4d2699b4a86332852e))
* **pr-rebase:** refuse to rebase branches whose work already landed ([#747](https://github.com/otto-nation/otto-workbench/issues/747)) ([f045172](https://github.com/otto-nation/otto-workbench/commit/f0451724d55f68b4e1ec6db994687671b3a675ab))
* **pr-rebase:** stop --fix from force-pushing under --no-push ([#640](https://github.com/otto-nation/otto-workbench/issues/640)) ([458daef](https://github.com/otto-nation/otto-workbench/commit/458daef4bd12c651fd236c71471d7401b147d3fa))
* **pr-rebase:** stop checkout -B discarding unpushed commits ([#755](https://github.com/otto-nation/otto-workbench/issues/755)) ([0ecd38f](https://github.com/otto-nation/otto-workbench/commit/0ecd38f6b113c59a39ec49fc1393b542eafc264a))
* **pr-review:** include static analysis in posted review comments ([#578](https://github.com/otto-nation/otto-workbench/issues/578)) ([5c9e01c](https://github.com/otto-nation/otto-workbench/commit/5c9e01c7819287993583ca2e1a00522925b334d8))
* **pr-review:** stop filtering scout leads per group to preserve cache prefix ([#584](https://github.com/otto-nation/otto-workbench/issues/584)) ([1cfe6e9](https://github.com/otto-nation/otto-workbench/commit/1cfe6e9662d9937daea3aa1f06b05097bc6ffbfe))
* **pr-state:** let load_state survive a corrupt state file ([#673](https://github.com/otto-nation/otto-workbench/issues/673)) ([679a140](https://github.com/otto-nation/otto-workbench/commit/679a140b6cb49ca634044aede7b997db2fcf43fc))
* **pr:** apply the review findings carried over from [#597](https://github.com/otto-nation/otto-workbench/issues/597) ([#601](https://github.com/otto-nation/otto-workbench/issues/601)) ([131199b](https://github.com/otto-nation/otto-workbench/commit/131199bf44b75b56618ef9c7d84f48bd659e4a20))
* **pr:** count only the unattributed rows the summary table publishes ([#877](https://github.com/otto-nation/otto-workbench/issues/877)) ([d2d8626](https://github.com/otto-nation/otto-workbench/commit/d2d862681d97da5233aea81a20491b0905d13c2f))
* **pr:** count the fix replies `--finish` drains ([#723](https://github.com/otto-nation/otto-workbench/issues/723)) ([7b94692](https://github.com/otto-nation/otto-workbench/commit/7b946927e372463e108e8249775cb45038b14140))
* **pr:** detect unpushed commits in pr status dashboard ([#575](https://github.com/otto-nation/otto-workbench/issues/575)) ([4867d1d](https://github.com/otto-nation/otto-workbench/commit/4867d1d65199797de575ec836e706124ef29de39))
* **pr:** follow a rebase when the closeout checks its fix commit ([#966](https://github.com/otto-nation/otto-workbench/issues/966)) ([11c8cf9](https://github.com/otto-nation/otto-workbench/commit/11c8cf962c250be6d80e8b755e67fa96323d41e5))
* **pr:** frame a reviewer-prompted fix as addressed in response ([#843](https://github.com/otto-nation/otto-workbench/issues/843)) ([b498d60](https://github.com/otto-nation/otto-workbench/commit/b498d6035697f5730baf64128a6e9f9c7ec3a59d))
* **pr:** give thread-to-commit attribution one owner ([#841](https://github.com/otto-nation/otto-workbench/issues/841)) ([968517d](https://github.com/otto-nation/otto-workbench/commit/968517d843a074c8b558a6edc6830d5716d40660))
* **pr:** harden comment triage, add commit-aware describe ([#597](https://github.com/otto-nation/otto-workbench/issues/597)) ([5f8cde7](https://github.com/otto-nation/otto-workbench/commit/5f8cde74913c2499c387d814bac4bc0610b5e03b))
* **pr:** honor draft mode and edit replies on resolved threads ([#701](https://github.com/otto-nation/otto-workbench/issues/701)) ([024a87a](https://github.com/otto-nation/otto-workbench/commit/024a87a095530bbc408267173e62c25e0f5caf73))
* **pr:** keep hand-written replies and summary cells authoritative ([#835](https://github.com/otto-nation/otto-workbench/issues/835)) ([2f34e7e](https://github.com/otto-nation/otto-workbench/commit/2f34e7ea22287bf4fb48c9e8bf96aac520cc5025))
* **pr:** key each decomposed comment item's summary row apart ([#865](https://github.com/otto-nation/otto-workbench/issues/865)) ([fae5282](https://github.com/otto-nation/otto-workbench/commit/fae5282d96e818c67211b4d5a0766a6092f37e81))
* **pr:** rebase onto the PR's actual base ([#842](https://github.com/otto-nation/otto-workbench/issues/842)) ([27638fb](https://github.com/otto-nation/otto-workbench/commit/27638fb2013ee074b140dc6620f56a0cc39f32b4))
* **pr:** record resolved threads in the persisted comment tally ([#992](https://github.com/otto-nation/otto-workbench/issues/992)) ([1a88985](https://github.com/otto-nation/otto-workbench/commit/1a88985a51030e81653089d688111eaead5cd138))
* **pr:** resolve a row's commit from its own line history ([#882](https://github.com/otto-nation/otto-workbench/issues/882)) ([d317b57](https://github.com/otto-nation/otto-workbench/commit/d317b57a588d17fcfb3216024a5dd1b47ee9d987)), closes [#858](https://github.com/otto-nation/otto-workbench/issues/858)
* **pr:** resolve delegate flag arity before picking a target ([#700](https://github.com/otto-nation/otto-workbench/issues/700)) ([8791fbd](https://github.com/otto-nation/otto-workbench/commit/8791fbdfbbe6a528d2972c9a2a7ef282b78d4730))
* **pr:** route fix-pass GitHub writes through the post gate ([#837](https://github.com/otto-nation/otto-workbench/issues/837)) ([bccfc1b](https://github.com/otto-nation/otto-workbench/commit/bccfc1b8a2783d76ddb1059afae075d7b3fb99e0))
* **pr:** serialize concurrent runs on a worktree ([#659](https://github.com/otto-nation/otto-workbench/issues/659)) ([4536a3f](https://github.com/otto-nation/otto-workbench/commit/4536a3f2c1982dbf5ea0ccdd61518d6ec9b48b5a))
* **pr:** stop pr create from swallowing its flag values ([#745](https://github.com/otto-nation/otto-workbench/issues/745)) ([0a91f43](https://github.com/otto-nation/otto-workbench/commit/0a91f431dc2b07330b9f05ddb3ec64f894d74cf7))
* **pr:** stop resolving a feature branch to the default branch worktree ([#609](https://github.com/otto-nation/otto-workbench/issues/609)) ([92d77d9](https://github.com/otto-nation/otto-workbench/commit/92d77d9e98a834834a7674a75aea1dfe7d4ce034))
* **pr:** stop review's mode flags fetching and resetting the worktree ([#881](https://github.com/otto-nation/otto-workbench/issues/881)) ([1a41521](https://github.com/otto-nation/otto-workbench/commit/1a415213344f2f39833898a94a599fe637dce952))
* **pr:** surface the undelivered closeout in pr status ([#787](https://github.com/otto-nation/otto-workbench/issues/787)) ([d5db45a](https://github.com/otto-nation/otto-workbench/commit/d5db45ad008f9210a777a03d8aaa7741c5496c05))
* **push-intent:** a landed branch is not a push that vanished ([#1020](https://github.com/otto-nation/otto-workbench/issues/1020)) ([c0d54f7](https://github.com/otto-nation/otto-workbench/commit/c0d54f7d9ef40468666eba536a2f09b2365f2082))
* **rebase:** close resolution gaps in pr-rebase and review pipeline ([#627](https://github.com/otto-nation/otto-workbench/issues/627)) ([08e39af](https://github.com/otto-nation/otto-workbench/commit/08e39afeb1b86c4a9882acba26bd3ff2eedd6407))
* **review-threads:** attribute reconciled work per row, not per branch ([#1110](https://github.com/otto-nation/otto-workbench/issues/1110)) ([893f236](https://github.com/otto-nation/otto-workbench/commit/893f2365b16f668c1d14b8974daf01112d85b14c)), closes [#1096](https://github.com/otto-nation/otto-workbench/issues/1096)
* **review-threads:** render needs_human actions as prose ([#788](https://github.com/otto-nation/otto-workbench/issues/788)) ([1643f44](https://github.com/otto-nation/otto-workbench/commit/1643f448df284ed083c6f1a2914dd81c834a8759))
* **review-threads:** settle comment items via their source ([#789](https://github.com/otto-nation/otto-workbench/issues/789)) ([8ab7796](https://github.com/otto-nation/otto-workbench/commit/8ab77964360f226a818daa7957bcc4f618b470be))
* **review:** correct commit attribution and hold side effects on contested premises ([#746](https://github.com/otto-nation/otto-workbench/issues/746)) ([7393edd](https://github.com/otto-nation/otto-workbench/commit/7393edd6fb439b5e61b4f285d95374d18aa63afb))
* **review:** paginate review threads; share one fetcher ([#637](https://github.com/otto-nation/otto-workbench/issues/637)) ([b5b345c](https://github.com/otto-nation/otto-workbench/commit/b5b345cf0b3c0ca97ff7759a346625b40d0d5b74))
* **review:** patch every binding of a name through the module proxy ([#649](https://github.com/otto-nation/otto-workbench/issues/649)) ([2014f6a](https://github.com/otto-nation/otto-workbench/commit/2014f6affa3249e26395376e0444b7cf596afd1e))
* **review:** posting answers a fixed and a declined finding apart ([#1065](https://github.com/otto-nation/otto-workbench/issues/1065)) ([0493f91](https://github.com/otto-nation/otto-workbench/commit/0493f9151d2e130f043f227ce3b5b19384a7f286))
* **review:** reconcile prior findings through a disposition ledger ([#696](https://github.com/otto-nation/otto-workbench/issues/696)) ([bde0f4d](https://github.com/otto-nation/otto-workbench/commit/bde0f4dcd45429cb51ddcb8bd2be154bddd3e768))
* **review:** reconcile review prose with dropped findings ([#709](https://github.com/otto-nation/otto-workbench/issues/709)) ([34cbb1b](https://github.com/otto-nation/otto-workbench/commit/34cbb1b49af024e911b2d8535b9d8a94405f7d39))
* **review:** recover at the commit the failed run started from ([#606](https://github.com/otto-nation/otto-workbench/issues/606)) ([674c5d7](https://github.com/otto-nation/otto-workbench/commit/674c5d7d6e373dbd2508a5f007a96f2dc77f20ec))
* **review:** replace Write mandate with Edit; consolidate prompt blocks ([#591](https://github.com/otto-nation/otto-workbench/issues/591)) ([1b526e7](https://github.com/otto-nation/otto-workbench/commit/1b526e71dcb3be6dda5efe1d32ebc637fce6acf3))
* **review:** report deferred-issue non-delivery as closeout debt ([#836](https://github.com/otto-nation/otto-workbench/issues/836)) ([3b5a987](https://github.com/otto-nation/otto-workbench/commit/3b5a9871d1920b3e4e7bcb93544123eb30f2539b))
* **review:** strip File Triage; collapse static analysis in summary ([#605](https://github.com/otto-nation/otto-workbench/issues/605)) ([244f99d](https://github.com/otto-nation/otto-workbench/commit/244f99d12aa5db2737f1a727bd670c8b2f9b4911))
* **review:** support --recover with --self ([#589](https://github.com/otto-nation/otto-workbench/issues/589)) ([a109248](https://github.com/otto-nation/otto-workbench/commit/a10924870cf52e22d4abfc1f868b2636db9ded15))
* **review:** sweep intermediates after the fix pass ([#699](https://github.com/otto-nation/otto-workbench/issues/699)) ([25fe0a2](https://github.com/otto-nation/otto-workbench/commit/25fe0a2cf26b31499eeacb6035dd22d2452ec20b))
* **settings:** deny EnterWorktree to enforce wt usage ([#590](https://github.com/otto-nation/otto-workbench/issues/590)) ([d8da1ef](https://github.com/otto-nation/otto-workbench/commit/d8da1efd45c6517d7914d2f4ea876f4dd226784b))


### Code Refactoring

* **ai:** dissolve the review's shared-helper module ([#1052](https://github.com/otto-nation/otto-workbench/issues/1052)) ([281dea8](https://github.com/otto-nation/otto-workbench/commit/281dea8f5d449b48226a61d9028b5f4de52cec85))
* **ai:** extract Python lib from ai/claude/lib/ to ai/lib/ ([#582](https://github.com/otto-nation/otto-workbench/issues/582)) ([d893c3a](https://github.com/otto-nation/otto-workbench/commit/d893c3a2fef506d0e2f0a402763f33a5ca6faa26))
* **ai:** fold FixSummary's outcomes into FixRecord ([#1004](https://github.com/otto-nation/otto-workbench/issues/1004)) ([66dd0fb](https://github.com/otto-nation/otto-workbench/commit/66dd0fb587d540269678659dadb893d6415ba152))
* **ai:** give collection and its budget one owner ([#1082](https://github.com/otto-nation/otto-workbench/issues/1082)) ([8d06a4c](https://github.com/otto-nation/otto-workbench/commit/8d06a4c67383c8f042fd13a5a4a580a321ec7d6c))
* **ai:** give every domain a fix record ([#947](https://github.com/otto-nation/otto-workbench/issues/947)) ([e762508](https://github.com/otto-nation/otto-workbench/commit/e762508e68d38084b41700d19f950b37e23ba974))
* **ai:** give git one runner instead of 131 call sites ([#880](https://github.com/otto-nation/otto-workbench/issues/880)) ([5cf6e02](https://github.com/otto-nation/otto-workbench/commit/5cf6e02b378ef8ccdad9af9e10106b0c84fe0663))
* **ai:** give review_findings' residue owners; delete the module ([#1075](https://github.com/otto-nation/otto-workbench/issues/1075)) ([33f4823](https://github.com/otto-nation/otto-workbench/commit/33f48237fd9ad8b136c7355655b0a6200e1e6bb4))
* **ai:** give review_preflight's names owners; delete the module ([#1090](https://github.com/otto-nation/otto-workbench/issues/1090)) ([8063ffb](https://github.com/otto-nation/otto-workbench/commit/8063ffb9f0e80864c0be7434ed37936e39eaed54))
* **ai:** give the agent phase registry its own module and env keys ([#985](https://github.com/otto-nation/otto-workbench/issues/985)) ([e013a41](https://github.com/otto-nation/otto-workbench/commit/e013a4191e2d0d9928d371e2d39d0fc4d9b80792))
* **ai:** give the comment fix pass one tracking-file owner ([#996](https://github.com/otto-nation/otto-workbench/issues/996)) ([4f08107](https://github.com/otto-nation/otto-workbench/commit/4f081075c8464de152e31a8d6da3117a2d9dd0d7))
* **ai:** give the cross-cutting values one owner each ([#997](https://github.com/otto-nation/otto-workbench/issues/997)) ([cce8c4f](https://github.com/otto-nation/otto-workbench/commit/cce8c4f3b85ff49287d8fc26c396271d23c9b287))
* **ai:** give the pipeline state one owner and break the cycle ([#1012](https://github.com/otto-nation/otto-workbench/issues/1012)) ([8035c36](https://github.com/otto-nation/otto-workbench/commit/8035c364e19ec8eb450ebff9621d1c6a0ff8202b))
* **ai:** give the review's vocabulary its own module ([#1013](https://github.com/otto-nation/otto-workbench/issues/1013)) ([3ad3b47](https://github.com/otto-nation/otto-workbench/commit/3ad3b472047b6b5077e1b7a58a11f23f655582e1))
* **ai:** lift shared symbols below the layer boundary ([#1123](https://github.com/otto-nation/otto-workbench/issues/1123)) ([8af5f62](https://github.com/otto-nation/otto-workbench/commit/8af5f6293c56cdb0e7f4e9d39f3c29ca13d52327))
* **ai:** move the fix-pass push recoveries into the landing owner ([#995](https://github.com/otto-nation/otto-workbench/issues/995)) ([40799dd](https://github.com/otto-nation/otto-workbench/commit/40799dd440f2f27fe735d979f263601d16eed52c))
* **ai:** one fix engine, and all three passes on it ([#1000](https://github.com/otto-nation/otto-workbench/issues/1000)) ([89a22c1](https://github.com/otto-nation/otto-workbench/commit/89a22c16238ac3f8fbbbc19d9ad1b94c9c386361))
* **ai:** one owner for finding identity, budget and orchestration ([#1106](https://github.com/otto-nation/otto-workbench/issues/1106)) ([290b9b8](https://github.com/otto-nation/otto-workbench/commit/290b9b8650840f2048ff0811d71a772e3c49d027))
* **ai:** one owner for the review document's frame ([#1032](https://github.com/otto-nation/otto-workbench/issues/1032)) ([e3f9717](https://github.com/otto-nation/otto-workbench/commit/e3f9717c8ae00aa560ec1284d07852902bc6e171))
* **ai:** one owner for the review document's metadata header ([#1028](https://github.com/otto-nation/otto-workbench/issues/1028)) ([971729a](https://github.com/otto-nation/otto-workbench/commit/971729a23c6cd843d43ed9708181038aa639c80c)), closes [#907](https://github.com/otto-nation/otto-workbench/issues/907)
* **ai:** one owner for what happens to findings across reviews ([#1068](https://github.com/otto-nation/otto-workbench/issues/1068)) ([57a92a1](https://github.com/otto-nation/otto-workbench/commit/57a92a10b74bf07f328d2468e9b746cd45658a73))
* **ai:** one owner for where a review lives on disk ([#1050](https://github.com/otto-nation/otto-workbench/issues/1050)) ([4587fe4](https://github.com/otto-nation/otto-workbench/commit/4587fe48b52c9a88dc4a61b74a591da7e96b3212))
* **ai:** one owner for where a review's sections go ([#1046](https://github.com/otto-nation/otto-workbench/issues/1046)) ([911567a](https://github.com/otto-nation/otto-workbench/commit/911567a7f9805a33a600842078794319b3b2edd6))
* **ai:** prune review_common's dead surface; share dedup helpers ([#1008](https://github.com/otto-nation/otto-workbench/issues/1008)) ([9a0e395](https://github.com/otto-nation/otto-workbench/commit/9a0e3958ece1eeb46342681b89747533e81b8ad9))
* **ai:** put the fix passes on phases behind one invocation owner ([#990](https://github.com/otto-nation/otto-workbench/issues/990)) ([28f4cd9](https://github.com/otto-nation/otto-workbench/commit/28f4cd9d233c1d3e03f287ad867242a19e684901))
* **ai:** render the fix answer format from its parser ([#1003](https://github.com/otto-nation/otto-workbench/issues/1003)) ([bb2ee81](https://github.com/otto-nation/otto-workbench/commit/bb2ee8168dc3fc6f615f440e01a1402c01ed7b03))
* **ai:** repackage ai/lib into layers with one-way imports ([#1136](https://github.com/otto-nation/otto-workbench/issues/1136)) ([da63626](https://github.com/otto-nation/otto-workbench/commit/da6362661b25d5bf8eeb64f92cf3c28b48fa0039))
* **ai:** split pr_context into topology, sync, and context ([#1121](https://github.com/otto-nation/otto-workbench/issues/1121)) ([24371eb](https://github.com/otto-nation/otto-workbench/commit/24371ebbead50f1242309726837717be89c629a9))
* **ai:** split reply threads and prior review into own modules ([#1112](https://github.com/otto-nation/otto-workbench/issues/1112)) ([922425a](https://github.com/otto-nation/otto-workbench/commit/922425a37dd726eb8e1eeb65d22b837b6ccb8ab3))
* **ai:** split the phase inventory out of the vocabulary ([#991](https://github.com/otto-nation/otto-workbench/issues/991)) ([25f627a](https://github.com/otto-nation/otto-workbench/commit/25f627ae20c5850205feda5a66d8cbce04f4e1ab))
* **ai:** stop generating permissions into settings.json ([#799](https://github.com/otto-nation/otto-workbench/issues/799)) ([908f3e9](https://github.com/otto-nation/otto-workbench/commit/908f3e946d793d22031aa2f66a80cdefbe6d7923))
* **ai:** the meta.json sidecar has one owner ([#1030](https://github.com/otto-nation/otto-workbench/issues/1030)) ([b2d4259](https://github.com/otto-nation/otto-workbench/commit/b2d42598dc8867a8430d29213d6ceafe86324cff))
* **ai:** the phase declares its prompt template ([#1016](https://github.com/otto-nation/otto-workbench/issues/1016)) ([5d7a198](https://github.com/otto-nation/otto-workbench/commit/5d7a198f4970359adacc78db2703a9fa8e80898a))
* **ai:** the phase declares whether it can be switched off ([#1018](https://github.com/otto-nation/otto-workbench/issues/1018)) ([af201a9](https://github.com/otto-nation/otto-workbench/commit/af201a99affbf5d713ce6013938f4ad62c716121))
* **ai:** the review document answers what it says ([#1039](https://github.com/otto-nation/otto-workbench/issues/1039)) ([7d19ae8](https://github.com/otto-nation/otto-workbench/commit/7d19ae8a6487996e0f1306442394544cbe8aa210))
* **ai:** the review document owns one tally of its findings ([#1047](https://github.com/otto-nation/otto-workbench/issues/1047)) ([a40936e](https://github.com/otto-nation/otto-workbench/commit/a40936e05b8958b027dba962e7a52a3eeba35820))
* **ai:** the review's findings are read off the document ([#1042](https://github.com/otto-nation/otto-workbench/issues/1042)) ([60f8c24](https://github.com/otto-nation/otto-workbench/commit/60f8c24ee02918fff39abf1abc84030c12ee50b2))
* **ai:** type the comments thread ledger ([#948](https://github.com/otto-nation/otto-workbench/issues/948)) ([638891d](https://github.com/otto-nation/otto-workbench/commit/638891d966fb5ade9bfe791ba0ca379080515f45))
* **ai:** unify fix-pass round results and tighten review pipeline ([#1070](https://github.com/otto-nation/otto-workbench/issues/1070)) ([e188a75](https://github.com/otto-nation/otto-workbench/commit/e188a7531d494247435004ef55e96335532328f9))
* **claude:** extract the Bash PreToolUse guard into a script ([#691](https://github.com/otto-nation/otto-workbench/issues/691)) ([7c6cafe](https://github.com/otto-nation/otto-workbench/commit/7c6cafeee1e9bb4d3940dadef5bab0ac57bad61e))
* **config:** one owner each for what the config is, shows, and writes ([#1115](https://github.com/otto-nation/otto-workbench/issues/1115)) ([02b5521](https://github.com/otto-nation/otto-workbench/commit/02b55219e52b52494991ee247e55efd7521a98ea))
* **config:** resolve config scopes through one owner ([#1014](https://github.com/otto-nation/otto-workbench/issues/1014)) ([de9bf27](https://github.com/otto-nation/otto-workbench/commit/de9bf272c1793125fed9f85a33f022f0f1ddaf42))
* **gh:** give gh one client instead of 45 call sites ([#934](https://github.com/otto-nation/otto-workbench/issues/934)) ([2832db2](https://github.com/otto-nation/otto-workbench/commit/2832db24e369f5751415c8abdc5bbdc8fe19fdeb))
* **git:** resolve the shared git dir through one owner ([#1064](https://github.com/otto-nation/otto-workbench/issues/1064)) ([39e501c](https://github.com/otto-nation/otto-workbench/commit/39e501c8ebdc5a90f609c935f00c4095520777b3))
* **mcp:** drop the tool server's config file for the derived scan ([#867](https://github.com/otto-nation/otto-workbench/issues/867)) ([7c42453](https://github.com/otto-nation/otto-workbench/commit/7c42453f93d6afc2d564605ec303cfc85355e73a))
* **otto-log:** make the stats row and column named types ([#778](https://github.com/otto-nation/otto-workbench/issues/778)) ([d8066c1](https://github.com/otto-nation/otto-workbench/commit/d8066c1e8f4267e2a600173ffdc78e677502227c))
* **pr-state:** let serde and each domain own the schema ([#664](https://github.com/otto-nation/otto-workbench/issues/664)) ([915097f](https://github.com/otto-nation/otto-workbench/commit/915097fde8b4fc554604115f2b35506234b502cf))
* **pr:** declare dispatch needs per command ([#802](https://github.com/otto-nation/otto-workbench/issues/802)) ([7146cda](https://github.com/otto-nation/otto-workbench/commit/7146cda7692921956d5bef2e3b2adbd492bd1400))
* **pr:** drop the deprecated --resolve aliases for --finish ([#758](https://github.com/otto-nation/otto-workbench/issues/758)) ([fb4a2e0](https://github.com/otto-nation/otto-workbench/commit/fb4a2e0814be76c74436f14bb78d4d0f86608885))
* **pr:** extract status rendering to domain lib modules ([#571](https://github.com/otto-nation/otto-workbench/issues/571)) ([5838122](https://github.com/otto-nation/otto-workbench/commit/58381224c4207853909826e15150ede0d0580a7f))
* **pr:** keep per-worktree state in the worktree's git dir ([#681](https://github.com/otto-nation/otto-workbench/issues/681)) ([1e49894](https://github.com/otto-nation/otto-workbench/commit/1e498942b54e9b9b9b1428a837f97da3a5fa0c14))
* **pr:** let each domain render and judge itself ([#945](https://github.com/otto-nation/otto-workbench/issues/945)) ([a3db904](https://github.com/otto-nation/otto-workbench/commit/a3db9048aff82eb06d1666203afc53365f3cb5da))
* **pr:** split the PR domains out of pr_state ([#939](https://github.com/otto-nation/otto-workbench/issues/939)) ([9e09fff](https://github.com/otto-nation/otto-workbench/commit/9e09fff27507c42b67b6c3625bf3b77e68996600))
* **rebase:** pr-rebase adopts git_client and the land owner ([#1011](https://github.com/otto-nation/otto-workbench/issues/1011)) ([86610db](https://github.com/otto-nation/otto-workbench/commit/86610db2c6766a34465bdf5020cd3efe6d1927f3))
* **review:** config-driven section registry with auto-discovery ([#580](https://github.com/otto-nation/otto-workbench/issues/580)) ([dc9d7d5](https://github.com/otto-nation/otto-workbench/commit/dc9d7d54a2e05e3110ce75a4b5418886bc62a838))
* **review:** derive a phase's output artifact from the phase ([#674](https://github.com/otto-nation/otto-workbench/issues/674)) ([b6f1df3](https://github.com/otto-nation/otto-workbench/commit/b6f1df3bf41547db8ec22a7e3783429d48503a90))
* **review:** resolve the reviews root per call ([#717](https://github.com/otto-nation/otto-workbench/issues/717)) ([78c72cb](https://github.com/otto-nation/otto-workbench/commit/78c72cbb6ff7095375e8c1859e10d2413290f260))
* **review:** split review_pipeline along the typed seams ([#647](https://github.com/otto-nation/otto-workbench/issues/647)) ([d334b2d](https://github.com/otto-nation/otto-workbench/commit/d334b2d064a22cb389cfbf6eeff0bd8b182b4c07))
* **review:** type synthesis failures and the review-type vocabulary ([#652](https://github.com/otto-nation/otto-workbench/issues/652)) ([d509b02](https://github.com/otto-nation/otto-workbench/commit/d509b02580c3bb4d13fcc7e61b96ea1dab385fd8))
* **review:** type the phase and effort tables ([#604](https://github.com/otto-nation/otto-workbench/issues/604)) ([69bbc0e](https://github.com/otto-nation/otto-workbench/commit/69bbc0ef510881f16dcf4afbb6f26afb91225c4f))
* **review:** walk the reviews tree once, from meta.json ([#801](https://github.com/otto-nation/otto-workbench/issues/801)) ([96ea04a](https://github.com/otto-nation/otto-workbench/commit/96ea04a599526f926d21a4ff47cd223ca479a252))
* **trail:** unify the trail root; add a terminal summary event ([#730](https://github.com/otto-nation/otto-workbench/issues/730)) ([91f3038](https://github.com/otto-nation/otto-workbench/commit/91f30381cef043d6562390db2877797a6f44dae5))
* **workbench:** resolve config, state, and cache roots in one place ([#650](https://github.com/otto-nation/otto-workbench/issues/650)) ([852417d](https://github.com/otto-nation/otto-workbench/commit/852417dd72c87d98cd4b5992a118d06e91f1e84f))
* **workbench:** split the config and state roots ([#682](https://github.com/otto-nation/otto-workbench/issues/682)) ([6591bfe](https://github.com/otto-nation/otto-workbench/commit/6591bfeadd49b96f11bbe1c31510e126d09e152a))

## [1.42.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.41.1...otto-ai-tools-v1.42.0) (2026-08-03)


### Features

* **ci-check:** --wait mode with incremental reporting; fix log fallback ([#562](https://github.com/otto-nation/otto-workbench/issues/562)) ([57bfa11](https://github.com/otto-nation/otto-workbench/commit/57bfa11fb862365fb9c8bfa466a753b0959b1960))
* **review:** add verdict and status fields to post.jsonl ([#566](https://github.com/otto-nation/otto-workbench/issues/566)) ([7c58afd](https://github.com/otto-nation/otto-workbench/commit/7c58afd773ea55f9fcf35dc4bc46345a1bac792f))
* **review:** static analysis framework for review pipeline ([#565](https://github.com/otto-nation/otto-workbench/issues/565)) ([00de37f](https://github.com/otto-nation/otto-workbench/commit/00de37f98847ee3c721cd595e5526f39e823848c))


### Bug Fixes

* **pr-rebase:** handle detached HEAD worktrees ([#567](https://github.com/otto-nation/otto-workbench/issues/567)) ([bf6907f](https://github.com/otto-nation/otto-workbench/commit/bf6907fdd0a7e2e252ea96b7f2edbaad26352bd1))

## [1.41.1](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.41.0...otto-ai-tools-v1.41.1) (2026-07-28)


### Bug Fixes

* **review:** accurate cost, token, and duration tracking ([#557](https://github.com/otto-nation/otto-workbench/issues/557)) ([40f746d](https://github.com/otto-nation/otto-workbench/commit/40f746d4905de2af1047a44152f24d75fd4c2041))
* **review:** sonnet-only pipeline to prevent rate limiting ([#561](https://github.com/otto-nation/otto-workbench/issues/561)) ([c00121d](https://github.com/otto-nation/otto-workbench/commit/c00121d55954f6349ce5971d415cff675bccbb97))

## [1.41.0](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.40.4...otto-ai-tools-v1.41.0) (2026-07-27)


### Features

* add resolve-branch script for fuzzy branch resolution ([#263](https://github.com/otto-nation/otto-workbench/issues/263)) ([afd7e11](https://github.com/otto-nation/otto-workbench/commit/afd7e116c45375da7ea6016fd21d6c37474981e4))
* add review-thread-triage script for non-interactive PR thread classification ([#291](https://github.com/otto-nation/otto-workbench/issues/291)) ([073c0e5](https://github.com/otto-nation/otto-workbench/commit/073c0e5579ec3f6bc7f1fa6a0a182b91fb686def))
* add unified pr CLI with state framework ([#298](https://github.com/otto-nation/otto-workbench/issues/298)) ([8e90905](https://github.com/otto-nation/otto-workbench/commit/8e90905f48f6a2c523cc7eeb5edea3a4ad6022c0))
* add user override layer; improve claude-review workflow ([#46](https://github.com/otto-nation/otto-workbench/issues/46)) ([fb024b8](https://github.com/otto-nation/otto-workbench/commit/fb024b863dc40c2fd696208a3736acbefe13f184))
* add wt-cleanup script; extract docs; simplify shell control flow ([#49](https://github.com/otto-nation/otto-workbench/issues/49)) ([815414a](https://github.com/otto-nation/otto-workbench/commit/815414abf6ce220f4b81dc2131697f7fa0d60e12))
* **ai:** add --effort and --max-groups flags to claude-review ([#442](https://github.com/otto-nation/otto-workbench/issues/442)) ([313bf9a](https://github.com/otto-nation/otto-workbench/commit/313bf9a1c650b07b97ebd609c87a5b084aa4b2a6))
* **ai:** add ceiling convention, debt tracking, and reuse hooks ([#427](https://github.com/otto-nation/otto-workbench/issues/427)) ([555aedd](https://github.com/otto-nation/otto-workbench/commit/555aedd42160101657d810fd6b1acba1a7dc77b5))
* **ai:** add claude-review workflow; split tool context by loading mode ([#42](https://github.com/otto-nation/otto-workbench/issues/42)) ([24cb899](https://github.com/otto-nation/otto-workbench/commit/24cb899338210441ba417b3880bf0d2b2dfc4974))
* **ai:** add config export with profile-based filtering ([#151](https://github.com/otto-nation/otto-workbench/issues/151)) ([f827a16](https://github.com/otto-nation/otto-workbench/commit/f827a16a4ea06c70f666b075247de4259308d1a1))
* **ai:** add headroom token compression as AI sub-tool ([#307](https://github.com/otto-nation/otto-workbench/issues/307)) ([c282a31](https://github.com/otto-nation/otto-workbench/commit/c282a317d03a1ab1393d0f8d18ab05c7dc738fdd))
* **ai:** add retry hint when group agent hits max turns ([#450](https://github.com/otto-nation/otto-workbench/issues/450)) ([90ce0c3](https://github.com/otto-nation/otto-workbench/commit/90ce0c39f2e6bfdf7cedb4b1403e757c645fc39e))
* **ai:** add reviewer-lite agent for group/angles/fix phases ([#447](https://github.com/otto-nation/otto-workbench/issues/447)) ([5a6bfc6](https://github.com/otto-nation/otto-workbench/commit/5a6bfc6e143f4e96b7cb3278216ca056409a6eae))
* **ai:** add statusline, reference card, and subagent reuse injection ([#435](https://github.com/otto-nation/otto-workbench/issues/435)) ([7caf27a](https://github.com/otto-nation/otto-workbench/commit/7caf27addb072dcf6db0878febc2437c91734385))
* **ai:** allow claude CLI and plugin script permissions ([#308](https://github.com/otto-nation/otto-workbench/issues/308)) ([32a4204](https://github.com/otto-nation/otto-workbench/commit/32a42040a2d110082d45521b1eb4e236ab17b828))
* **ai:** ceiling convention, reuse levels, subagent propagation ([#428](https://github.com/otto-nation/otto-workbench/issues/428)) ([8c09249](https://github.com/otto-nation/otto-workbench/commit/8c092493856afd9b60bdc2030e84d0f4f2eb185e))
* **ai:** drop prior review from synthesis prompts ([#444](https://github.com/otto-nation/otto-workbench/issues/444)) ([9c2a422](https://github.com/otto-nation/otto-workbench/commit/9c2a4224fbf44214d78e9dc26ecd0be9650ada3b))
* **ai:** extract project context from preflight into template variable ([#445](https://github.com/otto-nation/otto-workbench/issues/445)) ([3bdd879](https://github.com/otto-nation/otto-workbench/commit/3bdd8792847a44b44d831625c14d6ef793da6817))
* **ai:** Pi backend follow-ups — skills, extensions, steer, thinking, providers ([#390](https://github.com/otto-nation/otto-workbench/issues/390)) ([96b8dd5](https://github.com/otto-nation/otto-workbench/commit/96b8dd5b89cec09419de299d873c1c695ad069df))
* **ai:** prefer merging review groups with shared directory prefix ([#451](https://github.com/otto-nation/otto-workbench/issues/451)) ([8f1a502](https://github.com/otto-nation/otto-workbench/commit/8f1a50297ca4335e8f65422101bc2717a1cf5602))
* **ai:** reorder group template for prompt cache alignment ([#446](https://github.com/otto-nation/otto-workbench/issues/446)) ([736349b](https://github.com/otto-nation/otto-workbench/commit/736349b809f11a269a8a64451de89bda204c523e))
* **ai:** scope delta, reply threads, and PR header per group ([#443](https://github.com/otto-nation/otto-workbench/issues/443)) ([8fe5693](https://github.com/otto-nation/otto-workbench/commit/8fe56930f35e518daad587f712bad40cc4de5f1b))
* **bin:** add gcloud-reauth script; claude-review usage stats ([#70](https://github.com/otto-nation/otto-workbench/issues/70)) ([651b058](https://github.com/otto-nation/otto-workbench/commit/651b058d10688fc63b90f3d5aa2364f9927ef57a))
* **brew:** add autoupdate tap; move review output to ~/.claude/reviews ([#41](https://github.com/otto-nation/otto-workbench/issues/41)) ([025d3c8](https://github.com/otto-nation/otto-workbench/commit/025d3c8bb8470a40eac76dade413678093326505))
* **brew:** replace headroom with rtk for token compression ([#417](https://github.com/otto-nation/otto-workbench/issues/417)) ([6355781](https://github.com/otto-nation/otto-workbench/commit/63557810a133b325ac05f62f0e5614b4d7e58efc))
* **ci-check:** add --branch flag; use resolve-branch in skills ([#285](https://github.com/otto-nation/otto-workbench/issues/285)) ([10e3705](https://github.com/otto-nation/otto-workbench/commit/10e37056480bdb2bbbe770895542d69f1e742bd1))
* **ci-check:** add --fix flag for AI-driven CI failure fixes ([#414](https://github.com/otto-nation/otto-workbench/issues/414)) ([a713c82](https://github.com/otto-nation/otto-workbench/commit/a713c82ea62c2bd1de1e00232e3559560376b064))
* **ci-check:** enrich failure context; parallelize API calls; remove confirmation gates ([#504](https://github.com/otto-nation/otto-workbench/issues/504)) ([1f7ffde](https://github.com/otto-nation/otto-workbench/commit/1f7ffdef2444e34ff65a7ed91d2a75331dc2c0f9))
* **ci-check:** extract failed step name, add drift log markers ([#491](https://github.com/otto-nation/otto-workbench/issues/491)) ([4747fb7](https://github.com/otto-nation/otto-workbench/commit/4747fb791d80ac16f1308388c5c633eb262cdc31))
* **ci-check:** improve extraction robustness and artifact fallback ([#539](https://github.com/otto-nation/otto-workbench/issues/539)) ([55f93a8](https://github.com/otto-nation/otto-workbench/commit/55f93a83792b9d6b339490d8e18e4b91673d1e77))
* **ci-check:** rebase onto main before fixing CI failures ([#526](https://github.com/otto-nation/otto-workbench/issues/526)) ([1a74710](https://github.com/otto-nation/otto-workbench/commit/1a747104550c3188de022e97a7c89e42d7fd1223))
* **ci-check:** structural log extraction; headline surfacing in dashboard ([#398](https://github.com/otto-nation/otto-workbench/issues/398)) ([55fb271](https://github.com/otto-nation/otto-workbench/commit/55fb2718e2c166d50faced6023d312e099e954f6))
* **ci-failures:** add CI failure analysis skill and ci-check CLI ([#280](https://github.com/otto-nation/otto-workbench/issues/280)) ([365d021](https://github.com/otto-nation/otto-workbench/commit/365d021bdf5ef0d42c25dc8a8f2b207614c06ddc))
* **ci-failures:** auto-fix without confirmation and enrich BUILD failure context ([#501](https://github.com/otto-nation/otto-workbench/issues/501)) ([f4fc928](https://github.com/otto-nation/otto-workbench/commit/f4fc928fbe84530df0a8a82d72b33bb2e63e3ed9))
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
* **pr-comments:** add --fix flag to triage and auto-fix review threads ([#360](https://github.com/otto-nation/otto-workbench/issues/360)) ([05227c4](https://github.com/otto-nation/otto-workbench/commit/05227c45f159a22f05321f599c626baf7b5ce3c4))
* **pr-comments:** add thread lifecycle tracking for multi-round reviews ([#226](https://github.com/otto-nation/otto-workbench/issues/226)) ([6b49dc6](https://github.com/otto-nation/otto-workbench/commit/6b49dc6fb2beb0abfd02fd189ba385da481aa17c))
* **pr-comments:** decompose top-level comments into trackable items ([#528](https://github.com/otto-nation/otto-workbench/issues/528)) ([9036ae1](https://github.com/otto-nation/otto-workbench/commit/9036ae19e3aba05c699a6890e5b9cb6d3710a8d5))
* **pr-comments:** deferred thread tracking, issue lifecycle, and thread resolution ([#488](https://github.com/otto-nation/otto-workbench/issues/488)) ([c0fc5b8](https://github.com/otto-nation/otto-workbench/commit/c0fc5b81dbd0e9b14729f2224b3c00c8c069cd50))
* **pr-comments:** give fix agent access to main worktree for baseline context ([#499](https://github.com/otto-nation/otto-workbench/issues/499)) ([1cacbc5](https://github.com/otto-nation/otto-workbench/commit/1cacbc53b5957098d78fb86c5730c4c5ae4c4bd4))
* **pr-rebase:** add AI-assisted conflict resolution via claude -p ([#355](https://github.com/otto-nation/otto-workbench/issues/355)) ([f1028b7](https://github.com/otto-nation/otto-workbench/commit/f1028b73835506178d4eb8ef5471b66a171074fd))
* **pr:** add rebase subcommand with AI-assisted conflict resolution ([#313](https://github.com/otto-nation/otto-workbench/issues/313)) ([368acb1](https://github.com/otto-nation/otto-workbench/commit/368acb1697483f275ac31235270c459289ace886))
* **pr:** fetch and reset worktree to remote before pr commands ([#456](https://github.com/otto-nation/otto-workbench/issues/456)) ([5beede8](https://github.com/otto-nation/otto-workbench/commit/5beede8b8327f7a399dfd71f335b3b5f5e505060))
* **promote:** add promote-scan script ([#185](https://github.com/otto-nation/otto-workbench/issues/185)) ([4d7659a](https://github.com/otto-nation/otto-workbench/commit/4d7659a501babbee251339da5fa5e18bd17b595c))
* **pr:** passthrough architecture; resolve-branch; triage and repair subcommands ([#299](https://github.com/otto-nation/otto-workbench/issues/299)) ([e956361](https://github.com/otto-nation/otto-workbench/commit/e9563619f6eace7b4031560fe77abf5d6e1dc06f))
* **registries:** add reverse bindir validation; register new tools ([#74](https://github.com/otto-nation/otto-workbench/issues/74)) ([4b17997](https://github.com/otto-nation/otto-workbench/commit/4b17997f300282283c02a278ef8c10322e2ab711))
* **registries:** derive Claude permissions from registry allow field ([#129](https://github.com/otto-nation/otto-workbench/issues/129)) ([e35c059](https://github.com/otto-nation/otto-workbench/commit/e35c05965b7c552c49413a087b82e5b80d387034))
* **retro:** add PR review feedback loop for rules improvement ([#224](https://github.com/otto-nation/otto-workbench/issues/224)) ([40ecb40](https://github.com/otto-nation/otto-workbench/commit/40ecb405e1903eea08b9fd2ad6d59f6215218924))
* **review-post:** add summary/verdict to body and improve nit formatting ([#496](https://github.com/otto-nation/otto-workbench/issues/496)) ([d5dfb1a](https://github.com/otto-nation/otto-workbench/commit/d5dfb1afa37994ba814285b0cb0ddcd5f6c10bc7))
* **review-post:** migrate tests to pytest; add API layer coverage ([#118](https://github.com/otto-nation/otto-workbench/issues/118)) ([93a155d](https://github.com/otto-nation/otto-workbench/commit/93a155dca8d9ba0a8eaaf17da9ebe65249ee9f7b))
* **review-threads:** post per-thread replies and summary after --fix ([#394](https://github.com/otto-nation/otto-workbench/issues/394)) ([ca8789d](https://github.com/otto-nation/otto-workbench/commit/ca8789def7afa13427afe838644f3cbaa4a5cdff))
* **review:** add code-review angles, auto-fix, and retro integration ([#230](https://github.com/otto-nation/otto-workbench/issues/230)) ([677344b](https://github.com/otto-nation/otto-workbench/commit/677344b16c40dce99caeee0a5f33ab7679e9c16c))
* **review:** add evidence verification, stable IDs, and posted comment dedup ([#166](https://github.com/otto-nation/otto-workbench/issues/166)) ([003e97a](https://github.com/otto-nation/otto-workbench/commit/003e97aa4ab9b2ea99e3d7315ccd23ec83f71e5e))
* **review:** add head_sha, head_ref, base_ref, review_type to JSON summary ([#235](https://github.com/otto-nation/otto-workbench/issues/235)) ([7643455](https://github.com/otto-nation/otto-workbench/commit/7643455dd22c7b3b89c81eb152fe2a665dd794d9))
* **review:** add lead scout, disprove gate, and review profiles ([#458](https://github.com/otto-nation/otto-workbench/issues/458)) ([ffbe6d2](https://github.com/otto-nation/otto-workbench/commit/ffbe6d238c5ba49bd53e03ac86685b4aa741face))
* **reviewer:** add re-review verification with thread-based resolution ([#502](https://github.com/otto-nation/otto-workbench/issues/502)) ([95d4958](https://github.com/otto-nation/otto-workbench/commit/95d495899e3a3394ae34c581ed2c4105f629b395))
* **reviewer:** add test quality and convention evaluation criteria ([#119](https://github.com/otto-nation/otto-workbench/issues/119)) ([45830d0](https://github.com/otto-nation/otto-workbench/commit/45830d0894b3a7defb0a3cfbf27b1b1bd4fc641c))
* **review:** integrate PR state and role awareness into review prompts ([#489](https://github.com/otto-nation/otto-workbench/issues/489)) ([6d0dfc2](https://github.com/otto-nation/otto-workbench/commit/6d0dfc2d5137e00c5c4d7955f30a4fc72850dfbf))
* **review:** separate cache tokens from fresh in usage summary ([#464](https://github.com/otto-nation/otto-workbench/issues/464)) ([9d5f08e](https://github.com/otto-nation/otto-workbench/commit/9d5f08ef018f29d6796e35620e73e3d70e5e1392))
* **review:** set review status to error when synthesis agent fails ([#459](https://github.com/otto-nation/otto-workbench/issues/459)) ([15e1b49](https://github.com/otto-nation/otto-workbench/commit/15e1b491dd992bcb16417b40cfdb5d9ba806c36e))
* **review:** severity registry with posting routing ([#267](https://github.com/otto-nation/otto-workbench/issues/267)) ([de11526](https://github.com/otto-nation/otto-workbench/commit/de11526285dc561cfa1b4c7b7972fd1559795bf3))
* **review:** show findings, verdict, and phase warnings in summary ([#481](https://github.com/otto-nation/otto-workbench/issues/481)) ([26c68f2](https://github.com/otto-nation/otto-workbench/commit/26c68f239723527ecf545782351bae32b80a69da))
* **rules:** add insights-driven rules; allow /tmp writes ([#103](https://github.com/otto-nation/otto-workbench/issues/103)) ([9b272ff](https://github.com/otto-nation/otto-workbench/commit/9b272ff2c4ded1bdf9e7349f8d94d3cc7cdbf191))
* **self-review-fix:** auto-commit applied fixes ([#270](https://github.com/otto-nation/otto-workbench/issues/270)) ([1399213](https://github.com/otto-nation/otto-workbench/commit/1399213ee8e306ab89edfc5503ccbc105ebf7383))
* **skills,permissions:** add Arguments sections; auto-sync permissions from registries ([#282](https://github.com/otto-nation/otto-workbench/issues/282)) ([51061d5](https://github.com/otto-nation/otto-workbench/commit/51061d5b003d357d623c9be02d07d59e15280a70))
* **skills:** add trigger/skip frontmatter fields to SKILL.md validation and docs ([#258](https://github.com/otto-nation/otto-workbench/issues/258)) ([c81cc89](https://github.com/otto-nation/otto-workbench/commit/c81cc89ef7a56ac19371701584878f27eda24302))
* **state:** add component installation state tracking ([#43](https://github.com/otto-nation/otto-workbench/issues/43)) ([a8b6f7a](https://github.com/otto-nation/otto-workbench/commit/a8b6f7a57f245365620e57b1dd884f0d2e599260))
* targeted install, worktrunk migration; improve review workflow ([#40](https://github.com/otto-nation/otto-workbench/issues/40)) ([2376694](https://github.com/otto-nation/otto-workbench/commit/23766940bca66dba159df4499085de3ca8617574))
* **trail:** add structured JSONL logging framework across AI scripts ([#375](https://github.com/otto-nation/otto-workbench/issues/375)) ([5d95f8d](https://github.com/otto-nation/otto-workbench/commit/5d95f8d8ebaae580f249edf9f273afa9985b3c60))
* **validate-nesting:** extend nesting depth validator to all languages ([#108](https://github.com/otto-nation/otto-workbench/issues/108)) ([4565cf1](https://github.com/otto-nation/otto-workbench/commit/4565cf1a5e1286058f771ffe2eff7cb084eda877))


### Bug Fixes

* add PreToolUse hook to block command substitution in Bash tool ([#319](https://github.com/otto-nation/otto-workbench/issues/319)) ([743915d](https://github.com/otto-nation/otto-workbench/commit/743915d3d254f5e2495dc01e4b0d961100067cd6))
* **ai:** add early-exit to ci-failures skill for no-failure cases ([#438](https://github.com/otto-nation/otto-workbench/issues/438)) ([11273a3](https://github.com/otto-nation/otto-workbench/commit/11273a3bebe68a95e5e919e011b7bb008c011f99))
* **ai:** auto-commit regenerated files when pr rebase push fails ([#433](https://github.com/otto-nation/otto-workbench/issues/433)) ([35ac22e](https://github.com/otto-nation/otto-workbench/commit/35ac22ea1dd13c773d35907c7a5847c9fa873eb1))
* **ai:** handle BrokenPipeError in subprocess stdin write ([#511](https://github.com/otto-nation/otto-workbench/issues/511)) ([a229135](https://github.com/otto-nation/otto-workbench/commit/a229135e81d905fadc83305be0afc32aa39c7e2a))
* **ai:** improve pr-rebase conflict resolution parse diagnostics ([#440](https://github.com/otto-nation/otto-workbench/issues/440)) ([61b6868](https://github.com/otto-nation/otto-workbench/commit/61b6868f25067a14cba08e19caaa9442e85ec2a8))
* **ai:** improve review-threads error handling for commit/push failures ([#423](https://github.com/otto-nation/otto-workbench/issues/423)) ([0d93f3f](https://github.com/otto-nation/otto-workbench/commit/0d93f3f6e33f51ed12216866b9c7cd6d3257c293))
* **ai:** prevent pr-rebase from aborting when next commit has conflicts ([#432](https://github.com/otto-nation/otto-workbench/issues/432)) ([a510a4e](https://github.com/otto-nation/otto-workbench/commit/a510a4ee642e6f9f11caee0445e6e57a26e6d6fc))
* **ai:** remove dead diff from review sidecar meta.json ([#448](https://github.com/otto-nation/otto-workbench/issues/448)) ([032a393](https://github.com/otto-nation/otto-workbench/commit/032a393531250a9b6ad8faa3ac3f46c1483404c4))
* **ai:** remove redundant WORKBENCH_DIR from migration ([#380](https://github.com/otto-nation/otto-workbench/issues/380)) ([ebce72a](https://github.com/otto-nation/otto-workbench/commit/ebce72a40364a87c22d4e5e7a0364244d65bc093))
* **ai:** skip non-failure jobs in ci-check ([#429](https://github.com/otto-nation/otto-workbench/issues/429)) ([a3ba315](https://github.com/otto-nation/otto-workbench/commit/a3ba31512caa4d03e931df7996ced9d01932e15f))
* **ai:** stop posting redundant summary; include issue comments in fix output ([#431](https://github.com/otto-nation/otto-workbench/issues/431)) ([24690a5](https://github.com/otto-nation/otto-workbench/commit/24690a5b431c5df15d04b33346d89b98da8e520b))
* **ai:** track source_run_id per failure in ci-check multi-run merging ([#434](https://github.com/otto-nation/otto-workbench/issues/434)) ([7e3435b](https://github.com/otto-nation/otto-workbench/commit/7e3435b2830eb6734e5f8ca94f08d602d5884cd1))
* allow bin/local/ scripts without permission prompts ([#277](https://github.com/otto-nation/otto-workbench/issues/277)) ([9640fec](https://github.com/otto-nation/otto-workbench/commit/9640fec7e706fe22335590ee4969df8d95ffc1f4))
* **anatomy:** support bare repo worktree layouts ([#274](https://github.com/otto-nation/otto-workbench/issues/274)) ([ddc7df5](https://github.com/otto-nation/otto-workbench/commit/ddc7df556d712fc6ac0710c314ac997c75819277))
* avoid bash parameter substitution in skill code blocks ([#237](https://github.com/otto-nation/otto-workbench/issues/237)) ([72e8d96](https://github.com/otto-nation/otto-workbench/commit/72e8d9604669f686ef93f88d6b1487a622e5b9b2))
* **ci-check:** deduplicate re-runs per workflow ([#347](https://github.com/otto-nation/otto-workbench/issues/347)) ([9368e6a](https://github.com/otto-nation/otto-workbench/commit/9368e6a791c3597a154b1e09aa8495adaae6fd51))
* **ci-check:** fetch all workflow runs for latest commit ([#284](https://github.com/otto-nation/otto-workbench/issues/284)) ([1e9b149](https://github.com/otto-nation/otto-workbench/commit/1e9b14974b32ca05da3fb7fe81f24695c5fdb5aa))
* **ci-check:** rebase after capturing failure data, not before ([#536](https://github.com/otto-nation/otto-workbench/issues/536)) ([9e8d614](https://github.com/otto-nation/otto-workbench/commit/9e8d61430c6124d470fbe6ce218e3bc08cf1cd88))
* **ci-check:** report in-progress runs instead of false success ([#531](https://github.com/otto-nation/otto-workbench/issues/531)) ([9da1d6a](https://github.com/otto-nation/otto-workbench/commit/9da1d6a203e79ac4c3b3f7ea67d9df51d96bd367))
* **ci-check:** treat skipped/cancelled runs as non-failures ([#365](https://github.com/otto-nation/otto-workbench/issues/365)) ([a827d11](https://github.com/otto-nation/otto-workbench/commit/a827d11ede7dea546fedf0f61a1b1a3df3daa6bb))
* **ci:** add claude-config-release dispatch to homelab ([#186](https://github.com/otto-nation/otto-workbench/issues/186)) ([3a500e0](https://github.com/otto-nation/otto-workbench/commit/3a500e0b6748d4cd45ca9a4d2ca0d57a7a8c283e))
* **ci:** dynamically include all review scripts and Python libs in tarball ([#249](https://github.com/otto-nation/otto-workbench/issues/249)) ([f47388c](https://github.com/otto-nation/otto-workbench/commit/f47388cd8f9080cf8d0936110bce973ea0c2bc9b))
* **ci:** improve failure diagnosis with per-job log extraction ([#320](https://github.com/otto-nation/otto-workbench/issues/320)) ([2390f1a](https://github.com/otto-nation/otto-workbench/commit/2390f1a530a867fcff5aa49a07fdacb7ac9b165d))
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
* **claude-review:** deterministic fix-pass summary via Finding diffing ([#396](https://github.com/otto-nation/otto-workbench/issues/396)) ([818a7ff](https://github.com/otto-nation/otto-workbench/commit/818a7ff11157cfb5187609295f08f627adcf7773))
* **claude-review:** disable skills during review to prevent context overflow ([#140](https://github.com/otto-nation/otto-workbench/issues/140)) ([0cabf46](https://github.com/otto-nation/otto-workbench/commit/0cabf46fc91057a830f9030fe9e454e571033984))
* **claude-review:** drop subject_type from inline comments ([#115](https://github.com/otto-nation/otto-workbench/issues/115)) ([64a792f](https://github.com/otto-nation/otto-workbench/commit/64a792ff37428f365cb66bb7569742364e758bb4))
* **claude-review:** evidence verification drops real findings; fix counting broken ([#372](https://github.com/otto-nation/otto-workbench/issues/372)) ([b3341d6](https://github.com/otto-nation/otto-workbench/commit/b3341d6a0250be4a612a9c2b616797b74f72479a))
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
* **cli:** enforce --repo and --pr/--branch flag conventions ([#339](https://github.com/otto-nation/otto-workbench/issues/339)) ([9d4bc89](https://github.com/otto-nation/otto-workbench/commit/9d4bc89ea43db26291fc9e2876fd392ed3d25a21))
* **comments:** defer fix summary comment until discussion is complete ([#470](https://github.com/otto-nation/otto-workbench/issues/470)) ([64330e6](https://github.com/otto-nation/otto-workbench/commit/64330e674e06ab573d2248c955e7c68688212417))
* **comments:** post dismissal replies for invalid suggestion threads ([#465](https://github.com/otto-nation/otto-workbench/issues/465)) ([c13e33e](https://github.com/otto-nation/otto-workbench/commit/c13e33e3fbc56d1dca480892d4121cba98e5ed2b))
* **comments:** surface review-level body comments in pr-comments ([#472](https://github.com/otto-nation/otto-workbench/issues/472)) ([41b471b](https://github.com/otto-nation/otto-workbench/commit/41b471bc20c500ff51cf400f9149b4ee041b6164))
* discover all bin scripts dynamically in tarball build ([#312](https://github.com/otto-nation/otto-workbench/issues/312)) ([3f379f6](https://github.com/otto-nation/otto-workbench/commit/3f379f68d5db22cebe18b034b1a07edf1ae40bcf))
* **dream,promote:** skip projects without memory/ in trigger checks ([#223](https://github.com/otto-nation/otto-workbench/issues/223)) ([cb45c51](https://github.com/otto-nation/otto-workbench/commit/cb45c51f0b18316e579b74cfa0ea971e2de02b6e))
* **dream:** per-project cooldowns; add lint-sweep and --draft flag ([#210](https://github.com/otto-nation/otto-workbench/issues/210)) ([d246939](https://github.com/otto-nation/otto-workbench/commit/d246939199ae9641ca8db93fa2503b3676c9be0e))
* enforce PR template usage via rule and hook ([#84](https://github.com/otto-nation/otto-workbench/issues/84)) ([ce9c45f](https://github.com/otto-nation/otto-workbench/commit/ce9c45f436c96fe9e5f6eb372279b0d2d34e127d))
* **git:** sync gitignore.global entries into ~/.config/git/ignore ([#388](https://github.com/otto-nation/otto-workbench/issues/388)) ([83648fb](https://github.com/otto-nation/otto-workbench/commit/83648fb82202bc28282b9cb460b7ed15b835434b))
* **git:** worktree hook delegation; refactor claude-review with poster agent ([#45](https://github.com/otto-nation/otto-workbench/issues/45)) ([c9c6126](https://github.com/otto-nation/otto-workbench/commit/c9c612653d82a90ed30b64416136e1704bbf52fc))
* **hooks:** reduce false positives in brace expansion and branch guard ([#369](https://github.com/otto-nation/otto-workbench/issues/369)) ([cc0f4a6](https://github.com/otto-nation/otto-workbench/commit/cc0f4a6ebe7385a00dfa73e5a0eb2341d584e7e4))
* **pr-comments:** add --repo-dir flag; improve skill discoverability ([#228](https://github.com/otto-nation/otto-workbench/issues/228)) ([e16530d](https://github.com/otto-nation/otto-workbench/commit/e16530da29fd84173814722bc6ada1075efca780))
* **pr-comments:** add permalinks for comment items and reviewer column in summary ([#544](https://github.com/otto-nation/otto-workbench/issues/544)) ([f0d98f5](https://github.com/otto-nation/otto-workbench/commit/f0d98f5973d95b8927e226d669d06080d8017e7b))
* **pr-comments:** add TRIGGER/SKIP criteria to skill description ([#257](https://github.com/otto-nation/otto-workbench/issues/257)) ([98875dd](https://github.com/otto-nation/otto-workbench/commit/98875dd6c7bfc22f697d245881bd8b3959eea413))
* **pr-comments:** handle AI preamble text before JSON in triage output ([#494](https://github.com/otto-nation/otto-workbench/issues/494)) ([687ab02](https://github.com/otto-nation/otto-workbench/commit/687ab02de9f003c290a42fb46d5c486974a5f2fa))
* **pr-comments:** include issue link in deferred summary rows ([#534](https://github.com/otto-nation/otto-workbench/issues/534)) ([2aa10ff](https://github.com/otto-nation/otto-workbench/commit/2aa10ffd06956632a9ae45e69743a02fa807bdec))
* **pr-comments:** post replies for already-addressed threads ([#519](https://github.com/otto-nation/otto-workbench/issues/519)) ([a934e04](https://github.com/otto-nation/otto-workbench/commit/a934e043b037192a91a3bbcdafa7a0801775292f))
* **pr-comments:** recover agent commit SHA when script commit fails ([#486](https://github.com/otto-nation/otto-workbench/issues/486)) ([fdf2c33](https://github.com/otto-nation/otto-workbench/commit/fdf2c3388cd0d529088bf02b0ce7538295653b9f))
* **pr-comments:** remove false-positive reconciliation; defer replies until --resolve ([#523](https://github.com/otto-nation/otto-workbench/issues/523)) ([a94bec5](https://github.com/otto-nation/otto-workbench/commit/a94bec5e574fc77128d07d8c450052320e4e87d4))
* **pr-comments:** remove file-level reconciliation that falsely resolves threads ([#540](https://github.com/otto-nation/otto-workbench/issues/540)) ([55993a6](https://github.com/otto-nation/otto-workbench/commit/55993a6060d3e3e42f000c41ab48a9708095799c))
* **pr-comments:** track seen issue-level discussion comments in state ([#453](https://github.com/otto-nation/otto-workbench/issues/453)) ([ef75eb5](https://github.com/otto-nation/otto-workbench/commit/ef75eb5403366510eb7f3f17cb0071a697ff1c6d))
* **pr-comments:** use resolve-branch for branch name arguments ([#290](https://github.com/otto-nation/otto-workbench/issues/290)) ([68f79db](https://github.com/otto-nation/otto-workbench/commit/68f79dba89d2978fbed57118bcba92868d6b866c))
* **pr-context:** skip update_to_remote when worktree is on a different branch ([#475](https://github.com/otto-nation/otto-workbench/issues/475)) ([5db16f5](https://github.com/otto-nation/otto-workbench/commit/5db16f583b40569b2ce9ac02554af01a5f98a467))
* **pr-context:** use fuzzy resolution for bare-repo worktree lookup ([#503](https://github.com/otto-nation/otto-workbench/issues/503)) ([0a9b57c](https://github.com/otto-nation/otto-workbench/commit/0a9b57c8180128dddd3ba3636872579d62d8580b))
* **pr-rebase:** auto-fix pre-push check failures after conflict resolution ([#553](https://github.com/otto-nation/otto-workbench/issues/553)) ([0bc93a0](https://github.com/otto-nation/otto-workbench/commit/0bc93a08fccbee1b7acc039a6953cb2ec57c214d))
* **pr-rebase:** auto-resolve generated files instead of AI resolution ([#547](https://github.com/otto-nation/otto-workbench/issues/547)) ([cc5da99](https://github.com/otto-nation/otto-workbench/commit/cc5da99398d40ba70b221a39e9a117764d26c287))
* **pr-rebase:** auto-stash dirty tree; stage all tidy changes; abort on continue failure ([#408](https://github.com/otto-nation/otto-workbench/issues/408)) ([15572c6](https://github.com/otto-nation/otto-workbench/commit/15572c6912103d7dccd69d53a14e25bad0b1ba4c))
* **pr-rebase:** handle modify/delete conflicts without AI ([#509](https://github.com/otto-nation/otto-workbench/issues/509)) ([be1b833](https://github.com/otto-nation/otto-workbench/commit/be1b8335b68805e4325a29a684919f9ba5ff9110))
* **pr-rebase:** ignore untracked files in preflight dirty check ([#401](https://github.com/otto-nation/otto-workbench/issues/401)) ([45e529a](https://github.com/otto-nation/otto-workbench/commit/45e529a0c1863f4d3f8a982f70089d41d2b82be5))
* **pr-rebase:** resolve branch during rebase; surface AI prompt errors ([#506](https://github.com/otto-nation/otto-workbench/issues/506)) ([63b5f9d](https://github.com/otto-nation/otto-workbench/commit/63b5f9d8e6a55ab8be82cad50d10d25b939907bf))
* **pr-rebase:** resolve branch to worktree; default to --fix ([#374](https://github.com/otto-nation/otto-workbench/issues/374)) ([2e71b71](https://github.com/otto-nation/otto-workbench/commit/2e71b710adc86115b71001549ad0c7d0e71f58e4))
* **pr:** emit REVIEW_SUMMARY after successful review delegation ([#387](https://github.com/otto-nation/otto-workbench/issues/387)) ([d44c06e](https://github.com/otto-nation/otto-workbench/commit/d44c06e5d8854736ec002d7bdb6f2f76d6ef39e3))
* **pr:** forward only the user's original --pr or --branch flag ([#340](https://github.com/otto-nation/otto-workbench/issues/340)) ([e38529a](https://github.com/otto-nation/otto-workbench/commit/e38529a7ab17d6f7e1df2924669bc37eeb001f7b))
* **pr:** handle bare repos in pr_context.resolve() ([#364](https://github.com/otto-nation/otto-workbench/issues/364)) ([c315046](https://github.com/otto-nation/otto-workbench/commit/c3150468c25dfd91420771a2731569ef539e70b9))
* **pr:** handle SIGINT to prevent traceback on Ctrl+C ([#478](https://github.com/otto-nation/otto-workbench/issues/478)) ([a87522b](https://github.com/otto-nation/otto-workbench/commit/a87522b6ce51c974c08046321f2352a4d5ede7fc))
* **pr:** parse global flags regardless of position after subcommand ([#330](https://github.com/otto-nation/otto-workbench/issues/330)) ([50c5198](https://github.com/otto-nation/otto-workbench/commit/50c51989ca8627d77f2dccf28a5e2497015bf67d))
* **pr:** pass --help through to delegated scripts ([#325](https://github.com/otto-nation/otto-workbench/issues/325)) ([7be1293](https://github.com/otto-nation/otto-workbench/commit/7be12936d546b1341adf193f14b0140dcebd0daf))
* **pr:** prefer resolved PR number over --branch in delegate dispatch ([#538](https://github.com/otto-nation/otto-workbench/issues/538)) ([2d827f1](https://github.com/otto-nation/otto-workbench/commit/2d827f1fcf81d296848ec8f933104f438c69ee2a))
* **pr:** prevent --self injection when PR target comes from global flag or context ([#457](https://github.com/otto-nation/otto-workbench/issues/457)) ([52f19c0](https://github.com/otto-nation/otto-workbench/commit/52f19c00eb9253a2cc355ce52ce91d07f40a2cf7))
* **pr:** remove consumed positional from extra; skip stash mid-rebase ([#505](https://github.com/otto-nation/otto-workbench/issues/505)) ([3f6ebca](https://github.com/otto-nation/otto-workbench/commit/3f6ebcaa4df2b20d46089e81dd92e508829d61e5))
* **pr:** skip context resolution for help passthrough ([#328](https://github.com/otto-nation/otto-workbench/issues/328)) ([fc9a629](https://github.com/otto-nation/otto-workbench/commit/fc9a629def94e60e99972ecb2a5fcadb82188f4f))
* **rebase:** include base-side context for AI conflict resolution ([#471](https://github.com/otto-nation/otto-workbench/issues/471)) ([121cfd7](https://github.com/otto-nation/otto-workbench/commit/121cfd72d2d3d561b96ab264a76ebafcecd1ab1d))
* remove invalid Write path permission rules ([#551](https://github.com/otto-nation/otto-workbench/issues/551)) ([220382f](https://github.com/otto-nation/otto-workbench/commit/220382f80661f7e2c2a590fc9eda2e4b99dda0e5))
* **retro-scan:** use Path.home() for robust home resolution ([#530](https://github.com/otto-nation/otto-workbench/issues/530)) ([2b1a3c6](https://github.com/otto-nation/otto-workbench/commit/2b1a3c6660c74d4ce5102d3013b81dd111e53286))
* **retro:** distinguish global vs project rules in placement ([#529](https://github.com/otto-nation/otto-workbench/issues/529)) ([c31e305](https://github.com/otto-nation/otto-workbench/commit/c31e30504dd82018d45ff455b8b2e9ec470aa58b))
* **review-orchestrate:** include uncommitted changes in self-review metadata ([#120](https://github.com/otto-nation/otto-workbench/issues/120)) ([51989ca](https://github.com/otto-nation/otto-workbench/commit/51989caccb8b96459c01313ac52eb3044b13687c))
* **review-post:** chunk large reviews; improve rate limit retry ([#117](https://github.com/otto-nation/otto-workbench/issues/117)) ([be85ce8](https://github.com/otto-nation/otto-workbench/commit/be85ce8842fcf57fa49b7fd553a176add6b001d5))
* **review-post:** dedup, orphan cleanup; retry failed groups ([#219](https://github.com/otto-nation/otto-workbench/issues/219)) ([7fc0977](https://github.com/otto-nation/otto-workbench/commit/7fc0977c9a33af4e09e84660606a168115a7ab72))
* **review-post:** derive default severity filter from SEVERITY_LABELS ([#94](https://github.com/otto-nation/otto-workbench/issues/94)) ([37f0db6](https://github.com/otto-nation/otto-workbench/commit/37f0db61c077d3850a69d81156a8478e8f4776f5))
* **review-post:** fallback to body-level when inline lines can't be resolved ([#116](https://github.com/otto-nation/otto-workbench/issues/116)) ([8c311c0](https://github.com/otto-nation/otto-workbench/commit/8c311c082fba8f0feea477be9429372637af5273))
* **review-post:** handle large PRs, minimized reviews, write errors ([#240](https://github.com/otto-nation/otto-workbench/issues/240)) ([9804ec1](https://github.com/otto-nation/otto-workbench/commit/9804ec16b162082366cda6f734e6bc5b0eea843a))
* **review-post:** prevent double-finalization from dropping finding body text ([#86](https://github.com/otto-nation/otto-workbench/issues/86)) ([574aa51](https://github.com/otto-nation/otto-workbench/commit/574aa5148c8ee63fb537755558dc18b914d668e1))
* **review-post:** propagate sidecar repo to args.repo ([#261](https://github.com/otto-nation/otto-workbench/issues/261)) ([12a6ec1](https://github.com/otto-nation/otto-workbench/commit/12a6ec11dc4ec342a1dd6384bb45e0cf3f48cafb))
* **review-post:** re-verify inline positions on SHA drift instead of falling back to comment ([#527](https://github.com/otto-nation/otto-workbench/issues/527)) ([7f8479a](https://github.com/otto-nation/otto-workbench/commit/7f8479a592873c8186ee748a5d6b779f196fb75f))
* **review-post:** validate end_line against diff hunks for multi-line comments ([#121](https://github.com/otto-nation/otto-workbench/issues/121)) ([d02ad30](https://github.com/otto-nation/otto-workbench/commit/d02ad30556c9f3389b1f52aa7b9454b019443765))
* **review-post:** validate end_line against diff hunks for multi-line comments ([#131](https://github.com/otto-nation/otto-workbench/issues/131)) ([96c3862](https://github.com/otto-nation/otto-workbench/commit/96c38625cfa0f07d3d89ee83aaef1bfe22ec025f))
* **review-threads:** add --branch flag with resolve-branch support ([#335](https://github.com/otto-nation/otto-workbench/issues/335)) ([2a4b680](https://github.com/otto-nation/otto-workbench/commit/2a4b6806afb286837a7d542a676b11df2862bda6))
* **review-threads:** cast line field to int before arithmetic comparison ([#386](https://github.com/otto-nation/otto-workbench/issues/386)) ([3e2f725](https://github.com/otto-nation/otto-workbench/commit/3e2f725a9de2cd3f8e3a32fac0b7ef0bc89b1bb5))
* **review-threads:** commit regenerated files and surface non-inline comments ([#473](https://github.com/otto-nation/otto-workbench/issues/473)) ([0260dd7](https://github.com/otto-nation/otto-workbench/commit/0260dd75ade1ad3432b0afe3aebe59af500cc040))
* **review-threads:** strip markdown fences from AI triage JSON output ([#391](https://github.com/otto-nation/otto-workbench/issues/391)) ([80ccf14](https://github.com/otto-nation/otto-workbench/commit/80ccf14a6a17d51782dd1ab9b5148401c761c431))
* **review:** add git-native worktree fallback for self-review branch switch ([#176](https://github.com/otto-nation/otto-workbench/issues/176)) ([74e197e](https://github.com/otto-nation/otto-workbench/commit/74e197eda0ca54e802cb9b3f1d7a0982e7fa18c0))
* **review:** add missing sys import in review_pipeline ([#234](https://github.com/otto-nation/otto-workbench/issues/234)) ([dc879d5](https://github.com/otto-nation/otto-workbench/commit/dc879d5eafd3ab64b5f1d42b0af58747278ee8d5))
* **review:** classify positional targets as PR or branch before resolving context ([#466](https://github.com/otto-nation/otto-workbench/issues/466)) ([060e2a3](https://github.com/otto-nation/otto-workbench/commit/060e2a3e2134548f14b5d50add7fb9765aedd842))
* **review:** clean empty markers and fix stale verdict counts ([#178](https://github.com/otto-nation/otto-workbench/issues/178)) ([0b74247](https://github.com/otto-nation/otto-workbench/commit/0b7424749d3bd258965fcfaca0e3dd4687f7ded7))
* **review:** configurable diff floor; drop file contents on overflow ([#515](https://github.com/otto-nation/otto-workbench/issues/515)) ([25cf7a0](https://github.com/otto-nation/otto-workbench/commit/25cf7a0a2109dd60fb23a691f9412f22be1d8c6b))
* **review:** count fix-pass results from checkboxes instead of magic comment ([#329](https://github.com/otto-nation/otto-workbench/issues/329)) ([f8477c4](https://github.com/otto-nation/otto-workbench/commit/f8477c4a06831783ecea25d49eed06fe4b65ebb5))
* **review:** default _confirm to False when stdin is not interactive ([#516](https://github.com/otto-nation/otto-workbench/issues/516)) ([f6e1cdc](https://github.com/otto-nation/otto-workbench/commit/f6e1cdc19123760fd19d9a72377b35954453c70d))
* **review:** emit review_content in JSON summary; check agent exit codes ([#169](https://github.com/otto-nation/otto-workbench/issues/169)) ([c9bb122](https://github.com/otto-nation/otto-workbench/commit/c9bb1226fe6a3335f5660bccecfc47e87e3b70eb))
* **review:** enable disprove phase at medium effort ([#479](https://github.com/otto-nation/otto-workbench/issues/479)) ([4ad9dc8](https://github.com/otto-nation/otto-workbench/commit/4ad9dc892871820341f9e2a973c73d247ce5fc58))
* **review:** fall back to sonnet on opus quota exhaustion ([#554](https://github.com/otto-nation/otto-workbench/issues/554)) ([554b737](https://github.com/otto-nation/otto-workbench/commit/554b737975179e6652b4596be0a33d04cab998ca))
* **review:** grant write access to review file's parent directory ([#92](https://github.com/otto-nation/otto-workbench/issues/92)) ([1450e2b](https://github.com/otto-nation/otto-workbench/commit/1450e2bbc4e7922abc783a00c60ff78be905171b))
* **review:** improve orchestrate resilience for model errors and denied writes ([#183](https://github.com/otto-nation/otto-workbench/issues/183)) ([e4ae310](https://github.com/otto-nation/otto-workbench/commit/e4ae3105631969fdcd2196e1c4fc579980057b33))
* **review:** improve pipeline resilience and failure observability ([#550](https://github.com/otto-nation/otto-workbench/issues/550)) ([14b810d](https://github.com/otto-nation/otto-workbench/commit/14b810d92beadaf2b242290f0c1fb3e45f4cd7d6))
* **review:** improve review-post resilience for SHA drift and path-less findings ([#188](https://github.com/otto-nation/otto-workbench/issues/188)) ([50563d2](https://github.com/otto-nation/otto-workbench/commit/50563d262f1313dab55077c9f2ae62a033927706))
* **review:** improve synthesis resilience; eliminate permission denials ([#189](https://github.com/otto-nation/otto-workbench/issues/189)) ([151df4f](https://github.com/otto-nation/otto-workbench/commit/151df4fd15cae380e013d29776be42985ab18717))
* **review:** inject custom agent definitions in --bare mode ([#462](https://github.com/otto-nation/otto-workbench/issues/462)) ([b0e003f](https://github.com/otto-nation/otto-workbench/commit/b0e003f79ac2fd17ecb4963752c4f7df93ab0c10))
* **review:** log issue detection attempts before prompting ([#480](https://github.com/otto-nation/otto-workbench/issues/480)) ([8ee3401](https://github.com/otto-nation/otto-workbench/commit/8ee3401a1815d98d94c97c8f922f5f9423a6223e))
* **review:** mark review as error when group agents fail ([#482](https://github.com/otto-nation/otto-workbench/issues/482)) ([c1cf672](https://github.com/otto-nation/otto-workbench/commit/c1cf672d9a0e5a3edb4827a9f19ce126383281b3))
* **review:** preserve non-fallback worktrees after review ([#356](https://github.com/otto-nation/otto-workbench/issues/356)) ([6e66d01](https://github.com/otto-nation/otto-workbench/commit/6e66d01bcbedf6f41b596c6a88645a271fb2a18a))
* **review:** retry synthesis on transient API errors; detect self-review fallback ([#541](https://github.com/otto-nation/otto-workbench/issues/541)) ([89b02d9](https://github.com/otto-nation/otto-workbench/commit/89b02d92f79f5a227d8128096a5cd372c7fa3bbb))
* **review:** skip incremental delta when prior SHA equals HEAD ([#487](https://github.com/otto-nation/otto-workbench/issues/487)) ([517a3d5](https://github.com/otto-nation/otto-workbench/commit/517a3d591001c491e43ab570e0c2dbb8c5fceb91))
* **review:** stop auto-injecting --self for branch positionals and add review discovery fallback ([#463](https://github.com/otto-nation/otto-workbench/issues/463)) ([1d14547](https://github.com/otto-nation/otto-workbench/commit/1d145479e5918b10a6d842b7e96813dae4fea740))
* **review:** stop printing JSON summary to stdout, suppress false incomplete warnings ([#483](https://github.com/otto-nation/otto-workbench/issues/483)) ([d9edafb](https://github.com/otto-nation/otto-workbench/commit/d9edafb06b903e2cc7d511b0c4956fe005b23017))
* **review:** stop pruning merged reviews on every run ([#507](https://github.com/otto-nation/otto-workbench/issues/507)) ([d05cb4a](https://github.com/otto-nation/otto-workbench/commit/d05cb4a1219c8768822421fedac17dc94e80d281))
* **review:** strip bold-wrapped verdict action prefix before posting ([#520](https://github.com/otto-nation/otto-workbench/issues/520)) ([58cdd25](https://github.com/otto-nation/otto-workbench/commit/58cdd2556bd7223ab365fbeda996451defc883d5))
* **review:** strip unfenced blockquote evidence from review output ([#508](https://github.com/otto-nation/otto-workbench/issues/508)) ([eb7e201](https://github.com/otto-nation/otto-workbench/commit/eb7e201d7b867f7c94ee119981855b0221b4becd))
* **review:** strip verdict action prefix from posted review body ([#510](https://github.com/otto-nation/otto-workbench/issues/510)) ([0cbfb97](https://github.com/otto-nation/otto-workbench/commit/0cbfb97475549db4056157d68100c8924ada0b9e))
* **review:** support REPO_DIR env var for cross-repo usage ([#180](https://github.com/otto-nation/otto-workbench/issues/180)) ([1a3bcdb](https://github.com/otto-nation/otto-workbench/commit/1a3bcdb5e6473af6d603ad285a151ac02a9f6b97))
* **review:** use --bare for agent invocations to skip plugins and hooks ([#174](https://github.com/otto-nation/otto-workbench/issues/174)) ([c49d495](https://github.com/otto-nation/otto-workbench/commit/c49d495bf3a421877970988519e498d52a8cebeb))
* self-review findings ([#412](https://github.com/otto-nation/otto-workbench/issues/412)) ([0569472](https://github.com/otto-nation/otto-workbench/commit/0569472cccb6fa3207ff4ea2bd9651844b54c0fe))
* **self-review-fix:** avoid permission prompt from command substitution ([#268](https://github.com/otto-nation/otto-workbench/issues/268)) ([ce01a6a](https://github.com/otto-nation/otto-workbench/commit/ce01a6a51cbffb49f16fdd964c22ba3caab0ad58))
* **self-review-fix:** prevent permission prompts from fix-pass agent ([#269](https://github.com/otto-nation/otto-workbench/issues/269)) ([1761394](https://github.com/otto-nation/otto-workbench/commit/1761394df087fe467ba9ebb46f05f9d3d32efe37))
* **self-review-fix:** prevent stale reporting and fix-pass comment misplacement ([#266](https://github.com/otto-nation/otto-workbench/issues/266)) ([a55fd61](https://github.com/otto-nation/otto-workbench/commit/a55fd61fb82b8bbb638e2b9255dec74993916ce3))
* **settings:** add bare Read permission to auto-allow list ([#535](https://github.com/otto-nation/otto-workbench/issues/535)) ([57a164b](https://github.com/otto-nation/otto-workbench/commit/57a164bf76b64c9ee15f81bee91a0bf9dc539034))
* **settings:** add permission for skill scripts; document $VAR expansion ([#514](https://github.com/otto-nation/otto-workbench/issues/514)) ([8f9ada7](https://github.com/otto-nation/otto-workbench/commit/8f9ada760ae0875d96f3eb80e6ada60473c489ea))
* **skills:** escape PR reply bodies with heredoc pipe ([#110](https://github.com/otto-nation/otto-workbench/issues/110)) ([d2ac529](https://github.com/otto-nation/otto-workbench/commit/d2ac5294151f2d545ea0df1c38eea42d28411069))
* **skills:** handle bare repos and permission prompts in self-review-fix ([#242](https://github.com/otto-nation/otto-workbench/issues/242)) ([15ef7bc](https://github.com/otto-nation/otto-workbench/commit/15ef7bc1b605f86438ab95c862a8fd476b5276cf))
* **skills:** move sensitive-path file ops into scripts ([#221](https://github.com/otto-nation/otto-workbench/issues/221)) ([e20f765](https://github.com/otto-nation/otto-workbench/commit/e20f765e57a04706ae43197233d68807ab5d9846))
* **skill:** use Write tool for pr-rebase conflict resolution ([#343](https://github.com/otto-nation/otto-workbench/issues/343)) ([a1a4d87](https://github.com/otto-nation/otto-workbench/commit/a1a4d872c70d935218cd397fa800ac0bd3e65cf1))
* **trail:** add error coverage across pr CLI subcommands ([#393](https://github.com/otto-nation/otto-workbench/issues/393)) ([1fbc800](https://github.com/otto-nation/otto-workbench/commit/1fbc800cd395de4d633a94a75fc321c1a0c466fe))


### Performance Improvements

* **claude-review:** budget controls, scoped diffs; reduce review cost ([#98](https://github.com/otto-nation/otto-workbench/issues/98)) ([4f09035](https://github.com/otto-nation/otto-workbench/commit/4f090352c340725e4a5a2fa857edd1e0b0f5b63e))
* **claude-review:** optimize review pipeline and add metadata tracking ([#95](https://github.com/otto-nation/otto-workbench/issues/95)) ([8ea407a](https://github.com/otto-nation/otto-workbench/commit/8ea407a1fe873d3570a3e99733954580d6d173f8))


### Code Refactoring

* add context field to registries; clean up stale references ([#68](https://github.com/otto-nation/otto-workbench/issues/68)) ([0a52e1d](https://github.com/otto-nation/otto-workbench/commit/0a52e1d2090134989a805e37aa395f499d55c660))
* **ai:** centralize stderr output in log module ([#397](https://github.com/otto-nation/otto-workbench/issues/397)) ([5bcf726](https://github.com/otto-nation/otto-workbench/commit/5bcf72674a9f4dcdd26b18cee01b30b3fdcd3929))
* **ai:** extract AI backend abstraction for multi-backend support ([#383](https://github.com/otto-nation/otto-workbench/issues/383)) ([fa333e5](https://github.com/otto-nation/otto-workbench/commit/fa333e57411fdd68d1a43cd7bb21efe1273c0b95))
* **ai:** migrate GitHub REST reads to GraphQL; share PRData ([#368](https://github.com/otto-nation/otto-workbench/issues/368)) ([349c822](https://github.com/otto-nation/otto-workbench/commit/349c82289bfbc4c8d40ff00048118de6c6e8c3de))
* **ai:** parse delta file names from diff headers instead of separate git call ([#449](https://github.com/otto-nation/otto-workbench/issues/449)) ([a8020e9](https://github.com/otto-nation/otto-workbench/commit/a8020e9ca66d9e018f0c0dfa998cebf5c61631e8))
* **ai:** reduce post-processing file re-reads to single read/write ([#452](https://github.com/otto-nation/otto-workbench/issues/452)) ([9d05339](https://github.com/otto-nation/otto-workbench/commit/9d05339e564eab22d788766239321542c36c254f))
* **ai:** rename context skill and file to architecture ([#420](https://github.com/otto-nation/otto-workbench/issues/420)) ([1908959](https://github.com/otto-nation/otto-workbench/commit/190895900a6618f87bad8582d3a44b9883b71084))
* **auto-tasks:** run dream/promote/retro as headless sessions ([#233](https://github.com/otto-nation/otto-workbench/issues/233)) ([f889f3d](https://github.com/otto-nation/otto-workbench/commit/f889f3d4a8318b38480e6839e36598f9e6f159be))
* centralize output helpers; move usage text to usage() ([#72](https://github.com/otto-nation/otto-workbench/issues/72)) ([a73fba6](https://github.com/otto-nation/otto-workbench/commit/a73fba6f83d395dcdd1ca2691cf8768bdbee0394))
* **claude-review:** convert from bash to Python ([#338](https://github.com/otto-nation/otto-workbench/issues/338)) ([36d3926](https://github.com/otto-nation/otto-workbench/commit/36d392659889b3a44a5d1ca4601bc32193ecc662))
* **claude-review:** eliminate duplicate resolution; use pr_context.resolve() everywhere ([#345](https://github.com/otto-nation/otto-workbench/issues/345)) ([1146332](https://github.com/otto-nation/otto-workbench/commit/1146332f84316b4947a2e1d7300796c3f55b432c))
* **claude-review:** extract post logic into review-post ([#83](https://github.com/otto-nation/otto-workbench/issues/83)) ([5f58538](https://github.com/otto-nation/otto-workbench/commit/5f58538612749f996348824ef276fad0190947d1))
* **claude-review:** extract review-post into library modules ([#214](https://github.com/otto-nation/otto-workbench/issues/214)) ([719d9ee](https://github.com/otto-nation/otto-workbench/commit/719d9eec252c6f0553fad281e73caef645c59fe0))
* **claude:** replace poster agent with /pr-review skill ([#63](https://github.com/otto-nation/otto-workbench/issues/63)) ([42a6b69](https://github.com/otto-nation/otto-workbench/commit/42a6b698a477bcf1ef87f5893727ae9470792bd1))
* **cli:** move claude and override commands under ai subcommand ([#75](https://github.com/otto-nation/otto-workbench/issues/75)) ([c7f7e4c](https://github.com/otto-nation/otto-workbench/commit/c7f7e4c7011797b5bb8f61b6cef683a157d44d21))
* move Bash tool permission patterns from git-operations to bash-tool ([#321](https://github.com/otto-nation/otto-workbench/issues/321)) ([787c895](https://github.com/otto-nation/otto-workbench/commit/787c89542b9c7a0d2901fde4569b8159081dc821))
* **pr-comments:** consolidate thread model types ([#537](https://github.com/otto-nation/otto-workbench/issues/537)) ([48c4363](https://github.com/otto-nation/otto-workbench/commit/48c43632150eb743bd816870a60d2a6325f23757))
* **pr-rebase:** replace fragmented resume logic with _drive_to_completion loop ([#405](https://github.com/otto-nation/otto-workbench/issues/405)) ([e0d0046](https://github.com/otto-nation/otto-workbench/commit/e0d0046c3e01a278b444841d1dd521d05513bf4c))
* **pr:** eliminate double-dispatch; make pr the sole CLI entry point ([#351](https://github.com/otto-nation/otto-workbench/issues/351)) ([69ca53a](https://github.com/otto-nation/otto-workbench/commit/69ca53ab760197e1aa77c47c5c241af4c65b24ea))
* **pr:** migrate to script-owned state; improve CLI output ([#322](https://github.com/otto-nation/otto-workbench/issues/322)) ([a169747](https://github.com/otto-nation/otto-workbench/commit/a16974741e0e8e3abcdecee1de7a09682c3ffd37))
* redirect tool events to stderr; misc cleanups ([#411](https://github.com/otto-nation/otto-workbench/issues/411)) ([55e85bd](https://github.com/otto-nation/otto-workbench/commit/55e85bd058a01a4b36ee68911f20d783e67e7421))
* **registries:** define tool entry interface; require allow and context ([#292](https://github.com/otto-nation/otto-workbench/issues/292)) ([57f17f6](https://github.com/otto-nation/otto-workbench/commit/57f17f6e1f29854d318ccfd083197f4e42caa04f))
* **registries:** rename allow→permission, context→visibility; enforce conditional fields ([#296](https://github.com/otto-nation/otto-workbench/issues/296)) ([4718b3d](https://github.com/otto-nation/otto-workbench/commit/4718b3d29005429229ed0c85770a123c2bab9a4d))
* relocate user overrides from repo to XDG state dir ([7794730](https://github.com/otto-nation/otto-workbench/commit/77947302744edcd77826856122d60176ac461aab))
* rename autoupdate agent to maintenance; fix gh auth ([#348](https://github.com/otto-nation/otto-workbench/issues/348)) ([27d56a7](https://github.com/otto-nation/otto-workbench/commit/27d56a7d5b989ae77b491f297250f2efb750ef44))
* rename claude-review tarball to otto-ai-tools ([#314](https://github.com/otto-nation/otto-workbench/issues/314)) ([c4ed937](https://github.com/otto-nation/otto-workbench/commit/c4ed937648add4e4f4b418b4aaae70d77f637c4b))
* replace fragile ../ paths; centralize constants ([#254](https://github.com/otto-nation/otto-workbench/issues/254)) ([285d750](https://github.com/otto-nation/otto-workbench/commit/285d750bf8a02d26e5e17388960d85000c0fdde5))
* **retro:** extract helpers; scope cleanup to consumed reviews ([#413](https://github.com/otto-nation/otto-workbench/issues/413)) ([57857b9](https://github.com/otto-nation/otto-workbench/commit/57857b9e6ed1c5780efc5322686709b086b10cc1))
* **review:** absorb pr-comments-status into claude-review threads ([#232](https://github.com/otto-nation/otto-workbench/issues/232)) ([f23248d](https://github.com/otto-nation/otto-workbench/commit/f23248d756c9356033d8d23efaff416b124894ba))
* **review:** pre-create output files before launching agents ([#363](https://github.com/otto-nation/otto-workbench/issues/363)) ([ec806b6](https://github.com/otto-nation/otto-workbench/commit/ec806b66cdef1138acecc314ffe9e83aa65ac495))
* **review:** strengthen convention-awareness in review prompts ([#366](https://github.com/otto-nation/otto-workbench/issues/366)) ([2d5495b](https://github.com/otto-nation/otto-workbench/commit/2d5495b68dc599f5ea7edabf0e41cfd4dccaeed0))
* **self-review-fix:** use git remote instead of gh CLI for repo name ([#265](https://github.com/otto-nation/otto-workbench/issues/265)) ([7fc5c57](https://github.com/otto-nation/otto-workbench/commit/7fc5c57bd8131a6a84aba4af92b1b8ec2c2cf50c))
* **skills:** remove redundant resolve-branch and direct script calls ([#377](https://github.com/otto-nation/otto-workbench/issues/377)) ([caf1e2c](https://github.com/otto-nation/otto-workbench/commit/caf1e2c1c077ab9f9cdb00e2f9e373d8f92ef439))
* **workbench:** reorganize scripts, env management; add nesting validator and GPG setup ([#48](https://github.com/otto-nation/otto-workbench/issues/48)) ([fff0b20](https://github.com/otto-nation/otto-workbench/commit/fff0b20c64a1596a992e61d8e56920e255137432))

## [1.40.4](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.40.3...otto-ai-tools-v1.40.4) (2026-07-25)


### Bug Fixes

* remove invalid Write path permission rules ([#551](https://github.com/otto-nation/otto-workbench/issues/551)) ([220382f](https://github.com/otto-nation/otto-workbench/commit/220382f80661f7e2c2a590fc9eda2e4b99dda0e5))

## [1.40.3](https://github.com/otto-nation/otto-workbench/compare/otto-ai-tools-v1.40.2...otto-ai-tools-v1.40.3) (2026-07-24)


### Bug Fixes

* **pr-rebase:** auto-resolve generated files instead of AI resolution ([#547](https://github.com/otto-nation/otto-workbench/issues/547)) ([cc5da99](https://github.com/otto-nation/otto-workbench/commit/cc5da99398d40ba70b221a39e9a117764d26c287))
* **review:** improve pipeline resilience and failure observability ([#550](https://github.com/otto-nation/otto-workbench/issues/550)) ([14b810d](https://github.com/otto-nation/otto-workbench/commit/14b810d92beadaf2b242290f0c1fb3e45f4cd7d6))

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
