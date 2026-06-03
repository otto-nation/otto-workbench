# Changelog

## [1.10.1](https://github.com/otto-nation/otto-workbench/compare/v1.10.0...v1.10.1) (2026-06-03)


### Bug Fixes

* **claude-review:** auto-resume failed groups; fix diagnostics ([#159](https://github.com/otto-nation/otto-workbench/issues/159)) ([377a19d](https://github.com/otto-nation/otto-workbench/commit/377a19dd1fc8e171b007d714814527948ccb3003))
* **claude-review:** truncate diff for holistic/synthesis; fix dedup and formatting ([#157](https://github.com/otto-nation/otto-workbench/issues/157)) ([e45ca4b](https://github.com/otto-nation/otto-workbench/commit/e45ca4b2372151b9b893b5a2b0da7fbcea706d6b))

## [1.10.0](https://github.com/otto-nation/otto-workbench/compare/v1.9.0...v1.10.0) (2026-05-31)


### Features

* **ai:** add config export with profile-based filtering ([#151](https://github.com/otto-nation/otto-workbench/issues/151)) ([f827a16](https://github.com/otto-nation/otto-workbench/commit/f827a16a4ea06c70f666b075247de4259308d1a1))
* **rules:** add branch freshness, plan location; prefer xargs over find -exec ([#149](https://github.com/otto-nation/otto-workbench/issues/149)) ([a6d16c8](https://github.com/otto-nation/otto-workbench/commit/a6d16c8074ec911a1d6a91859d45958f38d294ff))

## [1.9.0](https://github.com/otto-nation/otto-workbench/compare/v1.8.0...v1.9.0) (2026-05-28)


### Features

* **claude-review:** dual-ref permalink resolution; consolidate GitHub API calls ([#147](https://github.com/otto-nation/otto-workbench/issues/147)) ([62e90dd](https://github.com/otto-nation/otto-workbench/commit/62e90ddea09581f5b714b8cedd6ff1850e7ec534))


### Bug Fixes

* **claude-review:** handle shallow clones; add metrics to JSON summary ([#146](https://github.com/otto-nation/otto-workbench/issues/146)) ([8585249](https://github.com/otto-nation/otto-workbench/commit/85852497a10e2843d875a9eb6faa3176df7462b6))

## [1.8.0](https://github.com/otto-nation/otto-workbench/compare/v1.7.0...v1.8.0) (2026-05-28)


### Features

* **rules:** save plan documents to ignore/plans/ ([#138](https://github.com/otto-nation/otto-workbench/issues/138)) ([4db5842](https://github.com/otto-nation/otto-workbench/commit/4db58424cb806cf1570e09321fcf84f6639b73c0))

## [1.7.0](https://github.com/otto-nation/otto-workbench/compare/v1.6.0...v1.7.0) (2026-05-27)


### Features

* **rules:** add rule to avoid compound cd commands ([#136](https://github.com/otto-nation/otto-workbench/issues/136)) ([565d2e1](https://github.com/otto-nation/otto-workbench/commit/565d2e10ea5fdeccbe4528ebea90ac8ae64f260a))


### Bug Fixes

* **release:** replace git clone with GitHub API for homebrew deploys ([#134](https://github.com/otto-nation/otto-workbench/issues/134)) ([5a22fce](https://github.com/otto-nation/otto-workbench/commit/5a22fce9403459fda8661261dbaf671f9a3a559c))

## [1.6.0](https://github.com/otto-nation/otto-workbench/compare/v1.5.0...v1.6.0) (2026-05-26)


### Features

* **claude-review:** add --json-summary flag for structured output ([#132](https://github.com/otto-nation/otto-workbench/issues/132)) ([5008079](https://github.com/otto-nation/otto-workbench/commit/5008079e20c7e38f695727bd7d8705d8add5a985))
* **registries:** derive Claude permissions from registry allow field ([#129](https://github.com/otto-nation/otto-workbench/issues/129)) ([e35c059](https://github.com/otto-nation/otto-workbench/commit/e35c05965b7c552c49413a087b82e5b80d387034))


### Bug Fixes

* **pre-push:** check all generated files; add ignore folder to .gitignore ([#128](https://github.com/otto-nation/otto-workbench/issues/128)) ([a4a3101](https://github.com/otto-nation/otto-workbench/commit/a4a3101ecdc21fc1a6c6da8c5803e93149c8cd6f))
* **review-post:** validate end_line against diff hunks for multi-line comments ([#131](https://github.com/otto-nation/otto-workbench/issues/131)) ([96c3862](https://github.com/otto-nation/otto-workbench/commit/96c38625cfa0f07d3d89ee83aaef1bfe22ec025f))

## [1.5.0](https://github.com/otto-nation/otto-workbench/compare/v1.4.0...v1.5.0) (2026-05-26)


### Features

* **claude-review:** add --resume flag; add validate-errexit lint ([#107](https://github.com/otto-nation/otto-workbench/issues/107)) ([69b8690](https://github.com/otto-nation/otto-workbench/commit/69b86909cef1f657537bf1df03baf2a88e9317a5))
* **claude-review:** add --resume to resume failed multi-phase reviews ([#106](https://github.com/otto-nation/otto-workbench/issues/106)) ([a068d06](https://github.com/otto-nation/otto-workbench/commit/a068d06b48910a508cb9e52292c65bde03e1c3ec))
* **claude-review:** add independent versioning and Homebrew formula ([#126](https://github.com/otto-nation/otto-workbench/issues/126)) ([f86f1c8](https://github.com/otto-nation/otto-workbench/commit/f86f1c8f680177e1358d7ba81fd16035251e4605))
* **git:** set global worktrunk worktree-path default ([#123](https://github.com/otto-nation/otto-workbench/issues/123)) ([3162f80](https://github.com/otto-nation/otto-workbench/commit/3162f804d6f4c05b264f21b9019a06fda5a011e7))
* **pr:** add --base flag to target a non-default base branch ([#112](https://github.com/otto-nation/otto-workbench/issues/112)) ([884dfe3](https://github.com/otto-nation/otto-workbench/commit/884dfe3beebb691efddf686cd01170275d1ff009))
* **review-post:** migrate tests to pytest; add API layer coverage ([#118](https://github.com/otto-nation/otto-workbench/issues/118)) ([93a155d](https://github.com/otto-nation/otto-workbench/commit/93a155dca8d9ba0a8eaaf17da9ebe65249ee9f7b))
* **rules:** add insights-driven rules; allow /tmp writes ([#103](https://github.com/otto-nation/otto-workbench/issues/103)) ([9b272ff](https://github.com/otto-nation/otto-workbench/commit/9b272ff2c4ded1bdf9e7349f8d94d3cc7cdbf191))
* **state:** replace installed.components with YAML-based install.yml ([#125](https://github.com/otto-nation/otto-workbench/issues/125)) ([3b71a55](https://github.com/otto-nation/otto-workbench/commit/3b71a55f742d77ea63d5d89ae190b2aca95dadee))
* **validate-nesting:** extend nesting depth validator to all languages ([#108](https://github.com/otto-nation/otto-workbench/issues/108)) ([4565cf1](https://github.com/otto-nation/otto-workbench/commit/4565cf1a5e1286058f771ffe2eff7cb084eda877))


### Bug Fixes

* **claude-review:** conditional preflight packing; ERR trap; set -e function pitfall ([#104](https://github.com/otto-nation/otto-workbench/issues/104)) ([9f4196e](https://github.com/otto-nation/otto-workbench/commit/9f4196ee301010f07eeaaf6803cb4fdcf604ef5f))
* **claude-review:** drop subject_type from inline comments ([#115](https://github.com/otto-nation/otto-workbench/issues/115)) ([64a792f](https://github.com/otto-nation/otto-workbench/commit/64a792ff37428f365cb66bb7569742364e758bb4))
* **claude-review:** fix review posting; reduce synthesis context ([#114](https://github.com/otto-nation/otto-workbench/issues/114)) ([c4a8e51](https://github.com/otto-nation/otto-workbench/commit/c4a8e51ebe2e582b26bb7a966147f9f5c1b41bef))
* **claude-review:** move self-review out of sensitive .claude/ dir ([#109](https://github.com/otto-nation/otto-workbench/issues/109)) ([8052151](https://github.com/otto-nation/otto-workbench/commit/8052151451c0d38bcd1ac89abadcb8304e696b8b))
* **pre-push:** check all generated files, not just tools.generated.md ([#113](https://github.com/otto-nation/otto-workbench/issues/113)) ([48d487f](https://github.com/otto-nation/otto-workbench/commit/48d487ff05ff5f8c7573d94ccbf989df1bc74921))
* **review-orchestrate:** include uncommitted changes in self-review metadata ([#120](https://github.com/otto-nation/otto-workbench/issues/120)) ([51989ca](https://github.com/otto-nation/otto-workbench/commit/51989caccb8b96459c01313ac52eb3044b13687c))
* **review-post:** chunk large reviews; improve rate limit retry ([#117](https://github.com/otto-nation/otto-workbench/issues/117)) ([be85ce8](https://github.com/otto-nation/otto-workbench/commit/be85ce8842fcf57fa49b7fd553a176add6b001d5))
* **review-post:** validate end_line against diff hunks for multi-line comments ([#121](https://github.com/otto-nation/otto-workbench/issues/121)) ([d02ad30](https://github.com/otto-nation/otto-workbench/commit/d02ad30556c9f3389b1f52aa7b9454b019443765))

## [1.4.0](https://github.com/otto-nation/otto-workbench/compare/v1.3.0...v1.4.0) (2026-05-18)


### Features

* **zsh:** export GITHUB_TOKEN from gh CLI credential ([#96](https://github.com/otto-nation/otto-workbench/issues/96)) ([cf20782](https://github.com/otto-nation/otto-workbench/commit/cf2078295ad58693ed5ec6ab539bbc5b9141ab2b))


### Bug Fixes

* **claude-review:** self-review archive, --force, and --no-post rule ([#100](https://github.com/otto-nation/otto-workbench/issues/100)) ([eeac16a](https://github.com/otto-nation/otto-workbench/commit/eeac16aa08dd38a9fa0747e5a3da88978688b597))
* **docker:** handle stale Colima socket after sleep/wake ([#99](https://github.com/otto-nation/otto-workbench/issues/99)) ([c49916a](https://github.com/otto-nation/otto-workbench/commit/c49916a54266368531a566edb5f4bae961499a9f))
* **install:** replace set -e-unsafe patterns in parse_install_flags ([#91](https://github.com/otto-nation/otto-workbench/issues/91)) ([9645607](https://github.com/otto-nation/otto-workbench/commit/9645607b979bba021ba864ea03185f02718d310c))
* **review-post:** derive default severity filter from SEVERITY_LABELS ([#94](https://github.com/otto-nation/otto-workbench/issues/94)) ([37f0db6](https://github.com/otto-nation/otto-workbench/commit/37f0db61c077d3850a69d81156a8478e8f4776f5))
* **review:** grant write access to review file's parent directory ([#92](https://github.com/otto-nation/otto-workbench/issues/92)) ([1450e2b](https://github.com/otto-nation/otto-workbench/commit/1450e2bbc4e7922abc783a00c60ff78be905171b))
* **setup:** restore execute bits and fix invocation in setup scripts ([#89](https://github.com/otto-nation/otto-workbench/issues/89)) ([82a581a](https://github.com/otto-nation/otto-workbench/commit/82a581a0c7780d64f6546be3ec936c9a866a936c))
* **wt-cleanup:** guard default branch by name, not just is_main flag ([#93](https://github.com/otto-nation/otto-workbench/issues/93)) ([15d2bef](https://github.com/otto-nation/otto-workbench/commit/15d2bef5829da26752b8cb910f1be9337e5aa311))


### Performance Improvements

* **claude-review:** budget controls, scoped diffs; reduce review cost ([#98](https://github.com/otto-nation/otto-workbench/issues/98)) ([4f09035](https://github.com/otto-nation/otto-workbench/commit/4f090352c340725e4a5a2fa857edd1e0b0f5b63e))
* **claude-review:** optimize review pipeline and add metadata tracking ([#95](https://github.com/otto-nation/otto-workbench/issues/95)) ([8ea407a](https://github.com/otto-nation/otto-workbench/commit/8ea407a1fe873d3570a3e99733954580d6d173f8))

## [1.3.0](https://github.com/otto-nation/otto-workbench/compare/v1.2.0...v1.3.0) (2026-05-15)


### Features

* **claude-review:** add preflight data collection to review agents ([#88](https://github.com/otto-nation/otto-workbench/issues/88)) ([8ee7bbd](https://github.com/otto-nation/otto-workbench/commit/8ee7bbde7cbba04dcf3fa510d243491a4801b3a1))


### Bug Fixes

* **review-post:** prevent double-finalization from dropping finding body text ([#86](https://github.com/otto-nation/otto-workbench/issues/86)) ([574aa51](https://github.com/otto-nation/otto-workbench/commit/574aa5148c8ee63fb537755558dc18b914d668e1))

## [1.2.0](https://github.com/otto-nation/otto-workbench/compare/v1.1.1...v1.2.0) (2026-05-15)


### Features

* **claude-review:** add language idioms analysis phase ([#85](https://github.com/otto-nation/otto-workbench/issues/85)) ([8023c3f](https://github.com/otto-nation/otto-workbench/commit/8023c3f411e9c79405340a76375aaf95e89ab9a3))
* **claude-review:** pre-flight checks; refactor(cli): noun-first ai syntax ([#80](https://github.com/otto-nation/otto-workbench/issues/80)) ([2516880](https://github.com/otto-nation/otto-workbench/commit/251688065e9e89cc3fd29aa2f6bfc935a1b8be1c))


### Bug Fixes

* enforce PR template usage via rule and hook ([#84](https://github.com/otto-nation/otto-workbench/issues/84)) ([ce9c45f](https://github.com/otto-nation/otto-workbench/commit/ce9c45f436c96fe9e5f6eb372279b0d2d34e127d))
* **wt-cleanup:** add grace period and dirty worktree protection ([#82](https://github.com/otto-nation/otto-workbench/issues/82)) ([63f24a9](https://github.com/otto-nation/otto-workbench/commit/63f24a9af91b0326402148ad744671dcad022801))


### Code Refactoring

* **claude-review:** extract post logic into review-post ([#83](https://github.com/otto-nation/otto-workbench/issues/83)) ([5f58538](https://github.com/otto-nation/otto-workbench/commit/5f58538612749f996348824ef276fad0190947d1))

## [1.1.1](https://github.com/otto-nation/otto-workbench/compare/v1.1.0...v1.1.1) (2026-05-15)


### Code Refactoring

* **cli:** switch ai override to noun-first syntax ([#79](https://github.com/otto-nation/otto-workbench/issues/79)) ([6102276](https://github.com/otto-nation/otto-workbench/commit/61022766d0a1c53342d3db05a5e8c708f6c30827))
* simplify component tiers; demote task to core, mise to optional ([#77](https://github.com/otto-nation/otto-workbench/issues/77)) ([0693642](https://github.com/otto-nation/otto-workbench/commit/069364215b50eba2be0ceca6caef845b513b349f))

## [1.1.0](https://github.com/otto-nation/otto-workbench/compare/v1.0.4...v1.1.0) (2026-05-15)


### Features

* **bin:** add gcloud-reauth script; claude-review usage stats ([#70](https://github.com/otto-nation/otto-workbench/issues/70)) ([651b058](https://github.com/otto-nation/otto-workbench/commit/651b058d10688fc63b90f3d5aa2364f9927ef57a))
* **claude-review:** add multi-phase parallel review for large PRs ([#69](https://github.com/otto-nation/otto-workbench/issues/69)) ([1540408](https://github.com/otto-nation/otto-workbench/commit/15404085c98d9e59bedd4477db827f65a892aaf2))
* **claude-review:** add self-review mode for pre-PR code review ([#71](https://github.com/otto-nation/otto-workbench/issues/71)) ([8177b90](https://github.com/otto-nation/otto-workbench/commit/8177b90d08b7d5279b1d3fa8025813174623bd77))
* **registries:** add reverse bindir validation; register new tools ([#74](https://github.com/otto-nation/otto-workbench/issues/74)) ([4b17997](https://github.com/otto-nation/otto-workbench/commit/4b17997f300282283c02a278ef8c10322e2ab711))
* **wt-cleanup:** detect squash-merged PRs via gh CLI fallback ([#76](https://github.com/otto-nation/otto-workbench/issues/76)) ([38c8e29](https://github.com/otto-nation/otto-workbench/commit/38c8e290cfcb7119de0359b2496b9e23e36224e2))


### Bug Fixes

* **claude-review:** use explicit prompt and skill file for post command ([#66](https://github.com/otto-nation/otto-workbench/issues/66)) ([792817d](https://github.com/otto-nation/otto-workbench/commit/792817d1168e434de4ed2fa46bed55c915d7bbb8))


### Code Refactoring

* add context field to registries; clean up stale references ([#68](https://github.com/otto-nation/otto-workbench/issues/68)) ([0a52e1d](https://github.com/otto-nation/otto-workbench/commit/0a52e1d2090134989a805e37aa395f499d55c660))
* centralize output helpers; move usage text to usage() ([#72](https://github.com/otto-nation/otto-workbench/issues/72)) ([a73fba6](https://github.com/otto-nation/otto-workbench/commit/a73fba6f83d395dcdd1ca2691cf8768bdbee0394))
* **cli:** move claude and override commands under ai subcommand ([#75](https://github.com/otto-nation/otto-workbench/issues/75)) ([c7f7e4c](https://github.com/otto-nation/otto-workbench/commit/c7f7e4c7011797b5bb8f61b6cef683a157d44d21))
* merge state subcommand into discover ([#73](https://github.com/otto-nation/otto-workbench/issues/73)) ([4530724](https://github.com/otto-nation/otto-workbench/commit/453072448605ff79dab7db83b52089b6ed86e48a))
* relocate user overrides from repo to XDG state dir ([7794730](https://github.com/otto-nation/otto-workbench/commit/77947302744edcd77826856122d60176ac461aab))

## [1.0.4](https://github.com/otto-nation/otto-workbench/compare/v1.0.3...v1.0.4) (2026-05-12)


### Code Refactoring

* **claude:** replace poster agent with /pr-review skill ([#63](https://github.com/otto-nation/otto-workbench/issues/63)) ([42a6b69](https://github.com/otto-nation/otto-workbench/commit/42a6b698a477bcf1ef87f5893727ae9470792bd1))

## [1.0.3](https://github.com/otto-nation/otto-workbench/compare/v1.0.2...v1.0.3) (2026-05-11)


### Bug Fixes

* scan ~/.local/bin for workbench scripts in aliases command ([#62](https://github.com/otto-nation/otto-workbench/issues/62)) ([dfeb5a2](https://github.com/otto-nation/otto-workbench/commit/dfeb5a29ff05d85abb6c5f03fbe1dc91b7738710))
* update aliases script for layered config.d structure ([#60](https://github.com/otto-nation/otto-workbench/issues/60)) ([7fe98ba](https://github.com/otto-nation/otto-workbench/commit/7fe98ba0c5ded2034b699b21c486c2af75acacfa))

## [1.0.2](https://github.com/otto-nation/otto-workbench/compare/v1.0.1...v1.0.2) (2026-05-11)


### Bug Fixes

* compute tarball SHA256 locally instead of re-downloading ([#58](https://github.com/otto-nation/otto-workbench/issues/58)) ([c27ca14](https://github.com/otto-nation/otto-workbench/commit/c27ca14c53bf5d93583cffea0afdaf027aea370a))

## [1.0.1](https://github.com/otto-nation/otto-workbench/compare/v1.0.0...v1.0.1) (2026-05-11)


### Bug Fixes

* write tarball to /tmp to avoid tar self-reference error ([#56](https://github.com/otto-nation/otto-workbench/issues/56)) ([ac49c6a](https://github.com/otto-nation/otto-workbench/commit/ac49c6ab5d93d4628b2cf0fb14e682f43050508f))

## 1.0.0 (2026-05-11)


### Features

* add auto-update via launchd; add wt-init for bare worktree conversion ([#50](https://github.com/otto-nation/otto-workbench/issues/50)) ([c4dc3d5](https://github.com/otto-nation/otto-workbench/commit/c4dc3d5af98be2722309587810703ffd53e53810))
* add brew setup, zsh template, AI guidelines, and docs improvements ([#5](https://github.com/otto-nation/otto-workbench/issues/5)) ([409aff2](https://github.com/otto-nation/otto-workbench/commit/409aff24cc76b81af7a7b1130428b092227c5055))
* add component registry, docker runtime selection, MCP manifests, and tooling improvements ([#12](https://github.com/otto-nation/otto-workbench/issues/12)) ([7297a13](https://github.com/otto-nation/otto-workbench/commit/7297a13aa82b830f572a567728f2b77309b09794))
* add mise, Docker alias switching, Ghostty migration framework; refactor brew and UI ([#27](https://github.com/otto-nation/otto-workbench/issues/27)) ([b88673b](https://github.com/otto-nation/otto-workbench/commit/b88673b924173335c45dbdf5de0863ae3fe687a1))
* add otto-workbench install command; slim install.sh to wrapper ([#53](https://github.com/otto-nation/otto-workbench/issues/53)) ([31276c2](https://github.com/otto-nation/otto-workbench/commit/31276c284175ff0ccb123ecb7e46332d04321361))
* add post-install summaries and select_menu for component prompts ([#14](https://github.com/otto-nation/otto-workbench/issues/14)) ([42002c5](https://github.com/otto-nation/otto-workbench/commit/42002c58e631389e3784f5adabf7e0f263e6d243))
* add release-please automation; add Homebrew formula ([#54](https://github.com/otto-nation/otto-workbench/issues/54)) ([ec15b37](https://github.com/otto-nation/otto-workbench/commit/ec15b379b19b81e5c27c59e481b645ac81491c20))
* add reword task; extract commit helpers to lib/ai-commit.sh ([cefa2be](https://github.com/otto-nation/otto-workbench/commit/cefa2be134bf8e830d21da6d284a4baaa99ea009))
* add Taskfile with sync capability ([ddbe7d1](https://github.com/otto-nation/otto-workbench/commit/ddbe7d1f56492480b07ef1e450afa92c8d5a98e1))
* add tool context registry, validation, and auto-generation ([#15](https://github.com/otto-nation/otto-workbench/issues/15)) ([7b724e5](https://github.com/otto-nation/otto-workbench/commit/7b724e5ca30e3f5f4af9e013ea5a1c41d29b1298))
* add user override layer; improve claude-review workflow ([#46](https://github.com/otto-nation/otto-workbench/issues/46)) ([fb024b8](https://github.com/otto-nation/otto-workbench/commit/fb024b863dc40c2fd696208a3736acbefe13f184))
* add worktree-stable symlink targets for bare repos ([#52](https://github.com/otto-nation/otto-workbench/issues/52)) ([df4a5c9](https://github.com/otto-nation/otto-workbench/commit/df4a5c983e5b24254f75f6b042e416ffedfe1cf2))
* add wt-cleanup script; extract docs; simplify shell control flow ([#49](https://github.com/otto-nation/otto-workbench/issues/49)) ([815414a](https://github.com/otto-nation/otto-workbench/commit/815414abf6ce220f4b81dc2131697f7fa0d60e12))
* **ai-commit:** extract helpers to lib, add CI, retry, and validation ([#1](https://github.com/otto-nation/otto-workbench/issues/1)) ([a3cabb8](https://github.com/otto-nation/otto-workbench/commit/a3cabb8aea76d6d2c40f8a513a548cee32c51cdc))
* **ai:** add agents, serena-mcp script; prune redundant rules ([#34](https://github.com/otto-nation/otto-workbench/issues/34)) ([d60d22f](https://github.com/otto-nation/otto-workbench/commit/d60d22f4a466c20580076fe2f3b34fb625028085))
* **ai:** add Claude agents, dream skill, and hook syncing ([#33](https://github.com/otto-nation/otto-workbench/issues/33)) ([6deddfa](https://github.com/otto-nation/otto-workbench/commit/6deddfa68019133f29406463ccee287ff7341671))
* **ai:** add claude-review workflow; split tool context by loading mode ([#42](https://github.com/otto-nation/otto-workbench/issues/42)) ([24cb899](https://github.com/otto-nation/otto-workbench/commit/24cb899338210441ba417b3880bf0d2b2dfc4974))
* **ai:** add coding guidelines, rule templates, init/rules bins, and workbench sync ([#13](https://github.com/otto-nation/otto-workbench/issues/13)) ([4bb2827](https://github.com/otto-nation/otto-workbench/commit/4bb2827112d693da90f7adcea0c2eba6b6432b4f))
* **ai:** add pr-review and analyze-project skills; generate public docs ([#38](https://github.com/otto-nation/otto-workbench/issues/38)) ([444e2f7](https://github.com/otto-nation/otto-workbench/commit/444e2f70dee9b6c6e79e25e7ca5a035bb9b566cb))
* **ai:** add second brain, memory backup, promote skill; harden CI and tooling ([#37](https://github.com/otto-nation/otto-workbench/issues/37)) ([0bfadd8](https://github.com/otto-nation/otto-workbench/commit/0bfadd896a6f4a5c52d428a133d37f16c8c5a780))
* **ai:** add setup script for Claude and Kiro tool configuration ([734de74](https://github.com/otto-nation/otto-workbench/commit/734de74d292edc5d2bfb5ba852c123da90da5a6b))
* **ai:** require source references in reviews; log local rule warnings ([#39](https://github.com/otto-nation/otto-workbench/issues/39)) ([1609fcc](https://github.com/otto-nation/otto-workbench/commit/1609fcc9bc8a4f3dc16a48db8cfe766c73395d35))
* **ai:** sync Claude settings, add MCPs, and skip already-installed items ([#11](https://github.com/otto-nation/otto-workbench/issues/11)) ([bba9fe8](https://github.com/otto-nation/otto-workbench/commit/bba9fe81cac5d56610477e7f8549820597061a76))
* auto-populate env defaults; replace static summaries with live status checks ([#51](https://github.com/otto-nation/otto-workbench/issues/51)) ([201f9ee](https://github.com/otto-nation/otto-workbench/commit/201f9eedfce8c558bee3f43d447b911f7a316543))
* **brew:** add autoupdate tap; move review output to ~/.claude/reviews ([#41](https://github.com/otto-nation/otto-workbench/issues/41)) ([025d3c8](https://github.com/otto-nation/otto-workbench/commit/025d3c8bb8470a40eac76dade413678093326505))
* **iterm:** add iTerm2 setup with Gruvbox themes and Fira Code font ([#8](https://github.com/otto-nation/otto-workbench/issues/8)) ([c95c8d4](https://github.com/otto-nation/otto-workbench/commit/c95c8d4ee51f9e26287f1a352777a5b37d21cf21))
* **pr:** add conditional issue closing with user confirmation ([bc04642](https://github.com/otto-nation/otto-workbench/commit/bc04642e5640b569b1df1a3a0e8c3b98f4febc21))
* **security:** add gitleaks scanning; extract git setup ([#19](https://github.com/otto-nation/otto-workbench/issues/19)) ([bfcd53d](https://github.com/otto-nation/otto-workbench/commit/bfcd53d54186b8eb5d86e4b534b3eae5bf70f7cf))
* **state:** add component installation state tracking ([#43](https://github.com/otto-nation/otto-workbench/issues/43)) ([a8b6f7a](https://github.com/otto-nation/otto-workbench/commit/a8b6f7a57f245365620e57b1dd884f0d2e599260))
* targeted install, worktrunk migration; improve review workflow ([#40](https://github.com/otto-nation/otto-workbench/issues/40)) ([2376694](https://github.com/otto-nation/otto-workbench/commit/23766940bca66dba159df4499085de3ca8617574))
* **task:** add wrapper script; fix working directory for git tasks ([934f30b](https://github.com/otto-nation/otto-workbench/commit/934f30b0e9b5e3a3fc8cbe91d633f78843f41789))
* **terminals:** consolidate terminal config, add secret model bootstrap ([#26](https://github.com/otto-nation/otto-workbench/issues/26)) ([3f0c944](https://github.com/otto-nation/otto-workbench/commit/3f0c944f189890b37546af554a4cb73f223b2f52))
* **ui:** add install_file and copy_dir; replace symlinks with copies ([#28](https://github.com/otto-nation/otto-workbench/issues/28)) ([8991b32](https://github.com/otto-nation/otto-workbench/commit/8991b322330a559e6a8cee772788f9b288eb5a44))
* workbench improvements — warnings, worktrees, component scripts, cleanup ([#36](https://github.com/otto-nation/otto-workbench/issues/36)) ([d357403](https://github.com/otto-nation/otto-workbench/commit/d357403221001ca8f4043636f62e6d62ff2b96b3))


### Bug Fixes

* **git:** worktree hook delegation; refactor claude-review with poster agent ([#45](https://github.com/otto-nation/otto-workbench/issues/45)) ([c9c6126](https://github.com/otto-nation/otto-workbench/commit/c9c612653d82a90ed30b64416136e1704bbf52fc))
* install global Taskfile to home directory for --global flag ([fb83596](https://github.com/otto-nation/otto-workbench/commit/fb8359616b3e9471ed276ee37a100e77afa93b41))
* **install:** use `task --global` flag for ai:setup command ([#10](https://github.com/otto-nation/otto-workbench/issues/10)) ([76f1820](https://github.com/otto-nation/otto-workbench/commit/76f182059a90f6a00aa8aa519d376a78d99ad7b5))
* **taskfile:** handle --assignee failure on repos without triage access ([5a246f1](https://github.com/otto-nation/otto-workbench/commit/5a246f1b022ef31503fbbba8b7e33579c0d0bbfc))
* **taskfile:** strip backticks from PR title; detect cross-fork PRs early ([f9a6ed4](https://github.com/otto-nation/otto-workbench/commit/f9a6ed42c0d85dd8e6a292a13d4901adbda0d79d))
* **taskfile:** strip markdown code blocks from AI commit message output ([285a273](https://github.com/otto-nation/otto-workbench/commit/285a27394daacc0caf489aaba5f50605a3f63f8b))
* **zed:** use python3 JSONC parser; add brew fpath before compinit ([#30](https://github.com/otto-nation/otto-workbench/issues/30)) ([7fcd622](https://github.com/otto-nation/otto-workbench/commit/7fcd622d6286ad29138d47358246b39e24df30cb))
