# Changelog

## [1.37.1](https://github.com/otto-nation/otto-workbench/compare/v1.37.0...v1.37.1) (2026-06-27)


### Bug Fixes

* **pr-rebase:** ignore untracked files in preflight dirty check ([#401](https://github.com/otto-nation/otto-workbench/issues/401)) ([45e529a](https://github.com/otto-nation/otto-workbench/commit/45e529a0c1863f4d3f8a982f70089d41d2b82be5))

## [1.37.0](https://github.com/otto-nation/otto-workbench/compare/v1.36.0...v1.37.0) (2026-06-26)


### Features

* **ci-check:** structural log extraction; headline surfacing in dashboard ([#398](https://github.com/otto-nation/otto-workbench/issues/398)) ([55fb271](https://github.com/otto-nation/otto-workbench/commit/55fb2718e2c166d50faced6023d312e099e954f6))

## [1.36.0](https://github.com/otto-nation/otto-workbench/compare/v1.35.1...v1.36.0) (2026-06-25)


### Features

* **ai:** Pi backend follow-ups — skills, extensions, steer, thinking, providers ([#390](https://github.com/otto-nation/otto-workbench/issues/390)) ([96b8dd5](https://github.com/otto-nation/otto-workbench/commit/96b8dd5b89cec09419de299d873c1c695ad069df))


### Bug Fixes

* **claude-review:** deterministic fix-pass summary via Finding diffing ([#396](https://github.com/otto-nation/otto-workbench/issues/396)) ([818a7ff](https://github.com/otto-nation/otto-workbench/commit/818a7ff11157cfb5187609295f08f627adcf7773))
* **git:** sync gitignore.global entries into ~/.config/git/ignore ([#388](https://github.com/otto-nation/otto-workbench/issues/388)) ([83648fb](https://github.com/otto-nation/otto-workbench/commit/83648fb82202bc28282b9cb460b7ed15b835434b))
* **review-threads:** strip markdown fences from AI triage JSON output ([#391](https://github.com/otto-nation/otto-workbench/issues/391)) ([80ccf14](https://github.com/otto-nation/otto-workbench/commit/80ccf14a6a17d51782dd1ab9b5148401c761c431))
* **validate-nesting:** detect extensionless python scripts via shebang ([#389](https://github.com/otto-nation/otto-workbench/issues/389)) ([77e3a35](https://github.com/otto-nation/otto-workbench/commit/77e3a35410690c11cd90b372c1bdaeab876ad6df))


### Code Refactoring

* **ai:** centralize stderr output in log module ([#397](https://github.com/otto-nation/otto-workbench/issues/397)) ([5bcf726](https://github.com/otto-nation/otto-workbench/commit/5bcf72674a9f4dcdd26b18cee01b30b3fdcd3929))
* **ai:** extract AI backend abstraction for multi-backend support ([#383](https://github.com/otto-nation/otto-workbench/issues/383)) ([fa333e5](https://github.com/otto-nation/otto-workbench/commit/fa333e57411fdd68d1a43cd7bb21efe1273c0b95))

## [1.35.1](https://github.com/otto-nation/otto-workbench/compare/v1.35.0...v1.35.1) (2026-06-25)


### Bug Fixes

* **ai:** remove redundant WORKBENCH_DIR from migration ([#380](https://github.com/otto-nation/otto-workbench/issues/380)) ([ebce72a](https://github.com/otto-nation/otto-workbench/commit/ebce72a40364a87c22d4e5e7a0364244d65bc093))

## [1.35.0](https://github.com/otto-nation/otto-workbench/compare/v1.34.0...v1.35.0) (2026-06-25)


### Features

* **maintenance:** add systemd user timer support for Linux ([#376](https://github.com/otto-nation/otto-workbench/issues/376)) ([969831c](https://github.com/otto-nation/otto-workbench/commit/969831c0a862d8c7897b6f8003a2d6d303f09c56))

## [1.34.0](https://github.com/otto-nation/otto-workbench/compare/v1.33.1...v1.34.0) (2026-06-25)


### Features

* **trail:** add structured JSONL logging framework across AI scripts ([#375](https://github.com/otto-nation/otto-workbench/issues/375)) ([5d95f8d](https://github.com/otto-nation/otto-workbench/commit/5d95f8d8ebaae580f249edf9f273afa9985b3c60))


### Bug Fixes

* **claude-review:** evidence verification drops real findings; fix counting broken ([#372](https://github.com/otto-nation/otto-workbench/issues/372)) ([b3341d6](https://github.com/otto-nation/otto-workbench/commit/b3341d6a0250be4a612a9c2b616797b74f72479a))
* **hooks:** reduce false positives in brace expansion and branch guard ([#369](https://github.com/otto-nation/otto-workbench/issues/369)) ([cc0f4a6](https://github.com/otto-nation/otto-workbench/commit/cc0f4a6ebe7385a00dfa73e5a0eb2341d584e7e4))
* **pr-rebase:** resolve branch to worktree; default to --fix ([#374](https://github.com/otto-nation/otto-workbench/issues/374)) ([2e71b71](https://github.com/otto-nation/otto-workbench/commit/2e71b710adc86115b71001549ad0c7d0e71f58e4))


### Code Refactoring

* **ai:** migrate GitHub REST reads to GraphQL; share PRData ([#368](https://github.com/otto-nation/otto-workbench/issues/368)) ([349c822](https://github.com/otto-nation/otto-workbench/commit/349c82289bfbc4c8d40ff00048118de6c6e8c3de))

## [1.33.1](https://github.com/otto-nation/otto-workbench/compare/v1.33.0...v1.33.1) (2026-06-24)


### Bug Fixes

* **ci-check:** treat skipped/cancelled runs as non-failures ([#365](https://github.com/otto-nation/otto-workbench/issues/365)) ([a827d11](https://github.com/otto-nation/otto-workbench/commit/a827d11ede7dea546fedf0f61a1b1a3df3daa6bb))
* **pr:** handle bare repos in pr_context.resolve() ([#364](https://github.com/otto-nation/otto-workbench/issues/364)) ([c315046](https://github.com/otto-nation/otto-workbench/commit/c3150468c25dfd91420771a2731569ef539e70b9))

## [1.33.0](https://github.com/otto-nation/otto-workbench/compare/v1.32.7...v1.33.0) (2026-06-24)


### Features

* add Linux server support for install and Docker sync ([#354](https://github.com/otto-nation/otto-workbench/issues/354)) ([9b6486e](https://github.com/otto-nation/otto-workbench/commit/9b6486eababe8e044ce43f87b277d7a549a5008b))
* **ai:** add Pi coding agent config component ([#353](https://github.com/otto-nation/otto-workbench/issues/353)) ([2e1eb7c](https://github.com/otto-nation/otto-workbench/commit/2e1eb7c0931e57fb2382461d448c6f1aebcd0d08))
* **pr-rebase:** add AI-assisted conflict resolution via claude -p ([#355](https://github.com/otto-nation/otto-workbench/issues/355)) ([f1028b7](https://github.com/otto-nation/otto-workbench/commit/f1028b73835506178d4eb8ef5471b66a171074fd))


### Bug Fixes

* **review:** preserve non-fallback worktrees after review ([#356](https://github.com/otto-nation/otto-workbench/issues/356)) ([6e66d01](https://github.com/otto-nation/otto-workbench/commit/6e66d01bcbedf6f41b596c6a88645a271fb2a18a))

## [1.32.7](https://github.com/otto-nation/otto-workbench/compare/v1.32.6...v1.32.7) (2026-06-23)


### Code Refactoring

* **pr:** eliminate double-dispatch; make pr the sole CLI entry point ([#351](https://github.com/otto-nation/otto-workbench/issues/351)) ([69ca53a](https://github.com/otto-nation/otto-workbench/commit/69ca53ab760197e1aa77c47c5c241af4c65b24ea))
* rename autoupdate agent to maintenance; fix gh auth ([#348](https://github.com/otto-nation/otto-workbench/issues/348)) ([27d56a7](https://github.com/otto-nation/otto-workbench/commit/27d56a7d5b989ae77b491f297250f2efb750ef44))

## [1.32.6](https://github.com/otto-nation/otto-workbench/compare/v1.32.5...v1.32.6) (2026-06-23)


### Bug Fixes

* **ci-check:** deduplicate re-runs per workflow ([#347](https://github.com/otto-nation/otto-workbench/issues/347)) ([9368e6a](https://github.com/otto-nation/otto-workbench/commit/9368e6a791c3597a154b1e09aa8495adaae6fd51))


### Code Refactoring

* **claude-review:** eliminate duplicate resolution; use pr_context.resolve() everywhere ([#345](https://github.com/otto-nation/otto-workbench/issues/345)) ([1146332](https://github.com/otto-nation/otto-workbench/commit/1146332f84316b4947a2e1d7300796c3f55b432c))

## [1.32.5](https://github.com/otto-nation/otto-workbench/compare/v1.32.4...v1.32.5) (2026-06-23)


### Bug Fixes

* **pr:** forward only the user's original --pr or --branch flag ([#340](https://github.com/otto-nation/otto-workbench/issues/340)) ([e38529a](https://github.com/otto-nation/otto-workbench/commit/e38529a7ab17d6f7e1df2924669bc37eeb001f7b))

## [1.32.4](https://github.com/otto-nation/otto-workbench/compare/v1.32.3...v1.32.4) (2026-06-23)


### Bug Fixes

* **cli:** enforce --repo and --pr/--branch flag conventions ([#339](https://github.com/otto-nation/otto-workbench/issues/339)) ([9d4bc89](https://github.com/otto-nation/otto-workbench/commit/9d4bc89ea43db26291fc9e2876fd392ed3d25a21))
* **release:** rename Homebrew formula from claude-review to otto-ai-tools ([#333](https://github.com/otto-nation/otto-workbench/issues/333)) ([15bd23f](https://github.com/otto-nation/otto-workbench/commit/15bd23f26a3ecbed91feabe40191cdd5f42928cd))
* **review-threads:** add --branch flag with resolve-branch support ([#335](https://github.com/otto-nation/otto-workbench/issues/335)) ([2a4b680](https://github.com/otto-nation/otto-workbench/commit/2a4b6806afb286837a7d542a676b11df2862bda6))


### Code Refactoring

* **claude-review:** convert from bash to Python ([#338](https://github.com/otto-nation/otto-workbench/issues/338)) ([36d3926](https://github.com/otto-nation/otto-workbench/commit/36d392659889b3a44a5d1ca4601bc32193ecc662))

## [1.32.3](https://github.com/otto-nation/otto-workbench/compare/v1.32.2...v1.32.3) (2026-06-22)


### Bug Fixes

* **pr:** parse global flags regardless of position after subcommand ([#330](https://github.com/otto-nation/otto-workbench/issues/330)) ([50c5198](https://github.com/otto-nation/otto-workbench/commit/50c51989ca8627d77f2dccf28a5e2497015bf67d))

## [1.32.2](https://github.com/otto-nation/otto-workbench/compare/v1.32.1...v1.32.2) (2026-06-22)


### Bug Fixes

* **pr:** pass --help through to delegated scripts ([#325](https://github.com/otto-nation/otto-workbench/issues/325)) ([7be1293](https://github.com/otto-nation/otto-workbench/commit/7be12936d546b1341adf193f14b0140dcebd0daf))
* **pr:** skip context resolution for help passthrough ([#328](https://github.com/otto-nation/otto-workbench/issues/328)) ([fc9a629](https://github.com/otto-nation/otto-workbench/commit/fc9a629def94e60e99972ecb2a5fcadb82188f4f))
* **review:** count fix-pass results from checkboxes instead of magic comment ([#329](https://github.com/otto-nation/otto-workbench/issues/329)) ([f8477c4](https://github.com/otto-nation/otto-workbench/commit/f8477c4a06831783ecea25d49eed06fe4b65ebb5))

## [1.32.1](https://github.com/otto-nation/otto-workbench/compare/v1.32.0...v1.32.1) (2026-06-22)


### Code Refactoring

* **pr:** migrate to script-owned state; improve CLI output ([#322](https://github.com/otto-nation/otto-workbench/issues/322)) ([a169747](https://github.com/otto-nation/otto-workbench/commit/a16974741e0e8e3abcdecee1de7a09682c3ffd37))

## [1.32.0](https://github.com/otto-nation/otto-workbench/compare/v1.31.0...v1.32.0) (2026-06-22)


### Features

* **pr:** add rebase subcommand with AI-assisted conflict resolution ([#313](https://github.com/otto-nation/otto-workbench/issues/313)) ([368acb1](https://github.com/otto-nation/otto-workbench/commit/368acb1697483f275ac31235270c459289ace886))


### Bug Fixes

* add PreToolUse hook to block command substitution in Bash tool ([#319](https://github.com/otto-nation/otto-workbench/issues/319)) ([743915d](https://github.com/otto-nation/otto-workbench/commit/743915d3d254f5e2495dc01e4b0d961100067cd6))
* **ci:** improve failure diagnosis with per-job log extraction ([#320](https://github.com/otto-nation/otto-workbench/issues/320)) ([2390f1a](https://github.com/otto-nation/otto-workbench/commit/2390f1a530a867fcff5aa49a07fdacb7ac9b165d))


### Code Refactoring

* move Bash tool permission patterns from git-operations to bash-tool ([#321](https://github.com/otto-nation/otto-workbench/issues/321)) ([787c895](https://github.com/otto-nation/otto-workbench/commit/787c89542b9c7a0d2901fde4569b8159081dc821))

## [1.31.0](https://github.com/otto-nation/otto-workbench/compare/v1.30.1...v1.31.0) (2026-06-22)


### Features

* **ai:** add headroom token compression as AI sub-tool ([#307](https://github.com/otto-nation/otto-workbench/issues/307)) ([c282a31](https://github.com/otto-nation/otto-workbench/commit/c282a317d03a1ab1393d0f8d18ab05c7dc738fdd))
* **claude-review:** wire reply threads into re-review prompts ([#309](https://github.com/otto-nation/otto-workbench/issues/309)) ([9d20ea8](https://github.com/otto-nation/otto-workbench/commit/9d20ea893908c98e46d244471d7dc799900537c4))


### Bug Fixes

* add .superpowers to gitignore ([#315](https://github.com/otto-nation/otto-workbench/issues/315)) ([e4d2646](https://github.com/otto-nation/otto-workbench/commit/e4d2646c6327b9ded399d98823df71d26e505d15))
* discover all bin scripts dynamically in tarball build ([#312](https://github.com/otto-nation/otto-workbench/issues/312)) ([3f379f6](https://github.com/otto-nation/otto-workbench/commit/3f379f68d5db22cebe18b034b1a07edf1ae40bcf))


### Code Refactoring

* rename claude-review tarball to otto-ai-tools ([#314](https://github.com/otto-nation/otto-workbench/issues/314)) ([c4ed937](https://github.com/otto-nation/otto-workbench/commit/c4ed937648add4e4f4b418b4aaae70d77f637c4b))

## [1.30.1](https://github.com/otto-nation/otto-workbench/compare/v1.30.0...v1.30.1) (2026-06-22)


### Bug Fixes

* **release:** auto-resolve manifest conflicts when updating release PRs ([#304](https://github.com/otto-nation/otto-workbench/issues/304)) ([47cd942](https://github.com/otto-nation/otto-workbench/commit/47cd942a55ac28044ad493403338736245ba09f1))
* **tests:** remove snapshot-and-compare safety check from test helper ([#302](https://github.com/otto-nation/otto-workbench/issues/302)) ([1fcf655](https://github.com/otto-nation/otto-workbench/commit/1fcf6558ea6c2f58655c8f4d2c70901dd7039ca4))

## [1.30.0](https://github.com/otto-nation/otto-workbench/compare/v1.29.0...v1.30.0) (2026-06-22)


### Features

* **pr:** passthrough architecture; resolve-branch; triage and repair subcommands ([#299](https://github.com/otto-nation/otto-workbench/issues/299)) ([e956361](https://github.com/otto-nation/otto-workbench/commit/e9563619f6eace7b4031560fe77abf5d6e1dc06f))

## [1.29.0](https://github.com/otto-nation/otto-workbench/compare/v1.28.0...v1.29.0) (2026-06-21)


### Features

* add unified pr CLI with state framework ([#298](https://github.com/otto-nation/otto-workbench/issues/298)) ([8e90905](https://github.com/otto-nation/otto-workbench/commit/8e90905f48f6a2c523cc7eeb5edea3a4ad6022c0))


### Bug Fixes

* **release:** update remaining release PRs after a release merges ([#293](https://github.com/otto-nation/otto-workbench/issues/293)) ([43a9b77](https://github.com/otto-nation/otto-workbench/commit/43a9b775479868d5cf179005a273185f480f8e77))


### Performance Improvements

* **tests:** cache expensive setup work in setup_file ([#295](https://github.com/otto-nation/otto-workbench/issues/295)) ([1d07ec8](https://github.com/otto-nation/otto-workbench/commit/1d07ec8ba5035c784581a2a5e93698597777c75c))


### Code Refactoring

* **registries:** rename allow→permission, context→visibility; enforce conditional fields ([#296](https://github.com/otto-nation/otto-workbench/issues/296)) ([4718b3d](https://github.com/otto-nation/otto-workbench/commit/4718b3d29005429229ed0c85770a123c2bab9a4d))

## [1.28.0](https://github.com/otto-nation/otto-workbench/compare/v1.27.0...v1.28.0) (2026-06-20)


### Features

* add review-thread-triage script for non-interactive PR thread classification ([#291](https://github.com/otto-nation/otto-workbench/issues/291)) ([073c0e5](https://github.com/otto-nation/otto-workbench/commit/073c0e5579ec3f6bc7f1fa6a0a182b91fb686def))
* **ci-check:** add --branch flag; use resolve-branch in skills ([#285](https://github.com/otto-nation/otto-workbench/issues/285)) ([10e3705](https://github.com/otto-nation/otto-workbench/commit/10e37056480bdb2bbbe770895542d69f1e742bd1))
* **ci-failures:** add CI failure analysis skill and ci-check CLI ([#280](https://github.com/otto-nation/otto-workbench/issues/280)) ([365d021](https://github.com/otto-nation/otto-workbench/commit/365d021bdf5ef0d42c25dc8a8f2b207614c06ddc))
* **hooks:** block brace expansion via PreToolUse hook ([#281](https://github.com/otto-nation/otto-workbench/issues/281)) ([3230133](https://github.com/otto-nation/otto-workbench/commit/3230133e4b94d1693a9255933e6c731ad5402665))
* **skills,permissions:** add Arguments sections; auto-sync permissions from registries ([#282](https://github.com/otto-nation/otto-workbench/issues/282)) ([51061d5](https://github.com/otto-nation/otto-workbench/commit/51061d5b003d357d623c9be02d07d59e15280a70))


### Bug Fixes

* allow bin/local/ scripts without permission prompts ([#277](https://github.com/otto-nation/otto-workbench/issues/277)) ([9640fec](https://github.com/otto-nation/otto-workbench/commit/9640fec7e706fe22335590ee4969df8d95ffc1f4))
* **claude-review:** scale max_turns when density filter omits files ([#289](https://github.com/otto-nation/otto-workbench/issues/289)) ([263d79b](https://github.com/otto-nation/otto-workbench/commit/263d79be275a8c1041b73545d676663063de4ad5))
* **pr-comments:** use resolve-branch for branch name arguments ([#290](https://github.com/otto-nation/otto-workbench/issues/290)) ([68f79db](https://github.com/otto-nation/otto-workbench/commit/68f79dba89d2978fbed57118bcba92868d6b866c))
* **release:** add backfill recovery; separate PRs per component ([#286](https://github.com/otto-nation/otto-workbench/issues/286)) ([d5cea29](https://github.com/otto-nation/otto-workbench/commit/d5cea29f85529afea322e22d39f678e9ef212eac))
* **sdd:** route all SDD artifacts to ignore/sdd/ instead of .git/ ([#279](https://github.com/otto-nation/otto-workbench/issues/279)) ([c460e68](https://github.com/otto-nation/otto-workbench/commit/c460e688132bbf9428df839c18afabab17414133))
* **tests:** isolate safety checks from concurrent worktrees ([#275](https://github.com/otto-nation/otto-workbench/issues/275)) ([2894e8d](https://github.com/otto-nation/otto-workbench/commit/2894e8da43c685e7c3c8cc5038f8c8e0acd7a7ad))
* **wt-cleanup:** add open-PR guard; fix integrated detection ([#276](https://github.com/otto-nation/otto-workbench/issues/276)) ([d7d7d14](https://github.com/otto-nation/otto-workbench/commit/d7d7d14ba63f0ad8f53e5acaf7e574a39dd765b6))


### Code Refactoring

* **registries:** define tool entry interface; require allow and context ([#292](https://github.com/otto-nation/otto-workbench/issues/292)) ([57f17f6](https://github.com/otto-nation/otto-workbench/commit/57f17f6e1f29854d318ccfd083197f4e42caa04f))

## [1.27.0](https://github.com/otto-nation/otto-workbench/compare/v1.26.0...v1.27.0) (2026-06-17)


### Features

* **wt-cleanup:** surface merged worktrees with uncommitted changes ([#272](https://github.com/otto-nation/otto-workbench/issues/272)) ([815fa3e](https://github.com/otto-nation/otto-workbench/commit/815fa3efffa7d035be0f8a7b9869d92819c1fb5a))

## [1.26.0](https://github.com/otto-nation/otto-workbench/compare/v1.25.0...v1.26.0) (2026-06-17)


### Features

* **self-review-fix:** auto-commit applied fixes ([#270](https://github.com/otto-nation/otto-workbench/issues/270)) ([1399213](https://github.com/otto-nation/otto-workbench/commit/1399213ee8e306ab89edfc5503ccbc105ebf7383))

## [1.25.0](https://github.com/otto-nation/otto-workbench/compare/v1.24.0...v1.25.0) (2026-06-17)


### Features

* add resolve-branch script for fuzzy branch resolution ([#263](https://github.com/otto-nation/otto-workbench/issues/263)) ([afd7e11](https://github.com/otto-nation/otto-workbench/commit/afd7e116c45375da7ea6016fd21d6c37474981e4))
* **review:** severity registry with posting routing ([#267](https://github.com/otto-nation/otto-workbench/issues/267)) ([de11526](https://github.com/otto-nation/otto-workbench/commit/de11526285dc561cfa1b4c7b7972fd1559795bf3))

## [1.24.0](https://github.com/otto-nation/otto-workbench/compare/v1.23.1...v1.24.0) (2026-06-16)


### Features

* **nesting:** add Go support; refactor into pluggable checker framework ([#260](https://github.com/otto-nation/otto-workbench/issues/260)) ([d7a9903](https://github.com/otto-nation/otto-workbench/commit/d7a9903f8e465bb078fc14bff2bee1acc0485637))
* **skills:** add trigger/skip frontmatter fields to SKILL.md validation and docs ([#258](https://github.com/otto-nation/otto-workbench/issues/258)) ([c81cc89](https://github.com/otto-nation/otto-workbench/commit/c81cc89ef7a56ac19371701584878f27eda24302))


### Bug Fixes

* **claude-review:** cleanup flags, self-review fixes; speed up tests ([#255](https://github.com/otto-nation/otto-workbench/issues/255)) ([48ea5f5](https://github.com/otto-nation/otto-workbench/commit/48ea5f5d57318975a19d398381581315c27c558c))
* **pr-comments:** add TRIGGER/SKIP criteria to skill description ([#257](https://github.com/otto-nation/otto-workbench/issues/257)) ([98875dd](https://github.com/otto-nation/otto-workbench/commit/98875dd6c7bfc22f697d245881bd8b3959eea413))


### Code Refactoring

* globalize validate-nesting; standardize lib/ui.sh sourcing ([#259](https://github.com/otto-nation/otto-workbench/issues/259)) ([e8ce861](https://github.com/otto-nation/otto-workbench/commit/e8ce861f7bac45c6a156928f6296b4ed18124495))
* replace fragile ../ paths; centralize constants ([#254](https://github.com/otto-nation/otto-workbench/issues/254)) ([285d750](https://github.com/otto-nation/otto-workbench/commit/285d750bf8a02d26e5e17388960d85000c0fdde5))

## [1.23.1](https://github.com/otto-nation/otto-workbench/compare/v1.23.0...v1.23.1) (2026-06-16)


### Bug Fixes

* **ci:** dynamically include all review scripts and Python libs in tarball ([#249](https://github.com/otto-nation/otto-workbench/issues/249)) ([f47388c](https://github.com/otto-nation/otto-workbench/commit/f47388cd8f9080cf8d0936110bce973ea0c2bc9b))
* **claude-review:** handle corrupt prompt-stats.json; speed up tests ([#248](https://github.com/otto-nation/otto-workbench/issues/248)) ([7606d85](https://github.com/otto-nation/otto-workbench/commit/7606d8504c6d16e27099c034b61e382aea1aba25))

## [1.23.0](https://github.com/otto-nation/otto-workbench/compare/v1.22.2...v1.23.0) (2026-06-16)


### Features

* **claude-review:** add rebuild subcommand ([#244](https://github.com/otto-nation/otto-workbench/issues/244)) ([657fe42](https://github.com/otto-nation/otto-workbench/commit/657fe421262dac20dc8d8f68e41c865d59adadf4))


### Bug Fixes

* **claude-review:** handle corrupt prompt-stats.json from concurrent writes ([#247](https://github.com/otto-nation/otto-workbench/issues/247)) ([a378db9](https://github.com/otto-nation/otto-workbench/commit/a378db9050b1b5380561c07f23fd908a525daa95))
* **claude-review:** reduce prompt bloat with density-based file skipping ([#245](https://github.com/otto-nation/otto-workbench/issues/245)) ([54846bd](https://github.com/otto-nation/otto-workbench/commit/54846bd4ec0098adc5e256636741cce99d051524))

## [1.22.2](https://github.com/otto-nation/otto-workbench/compare/v1.22.1...v1.22.2) (2026-06-15)


### Bug Fixes

* **skills:** handle bare repos and permission prompts in self-review-fix ([#242](https://github.com/otto-nation/otto-workbench/issues/242)) ([15ef7bc](https://github.com/otto-nation/otto-workbench/commit/15ef7bc1b605f86438ab95c862a8fd476b5276cf))

## [1.22.1](https://github.com/otto-nation/otto-workbench/compare/v1.22.0...v1.22.1) (2026-06-15)


### Bug Fixes

* **review-post:** handle large PRs, minimized reviews, write errors ([#240](https://github.com/otto-nation/otto-workbench/issues/240)) ([9804ec1](https://github.com/otto-nation/otto-workbench/commit/9804ec16b162082366cda6f734e6bc5b0eea843a))

## [1.22.0](https://github.com/otto-nation/otto-workbench/compare/v1.21.0...v1.22.0) (2026-06-15)


### Features

* **review:** add head_sha, head_ref, base_ref, review_type to JSON summary ([#235](https://github.com/otto-nation/otto-workbench/issues/235)) ([7643455](https://github.com/otto-nation/otto-workbench/commit/7643455dd22c7b3b89c81eb152fe2a665dd794d9))


### Bug Fixes

* avoid bash parameter substitution in skill code blocks ([#237](https://github.com/otto-nation/otto-workbench/issues/237)) ([72e8d96](https://github.com/otto-nation/otto-workbench/commit/72e8d9604669f686ef93f88d6b1487a622e5b9b2))

## [1.21.0](https://github.com/otto-nation/otto-workbench/compare/v1.20.0...v1.21.0) (2026-06-15)


### Features

* **review:** add code-review angles, auto-fix, and retro integration ([#230](https://github.com/otto-nation/otto-workbench/issues/230)) ([677344b](https://github.com/otto-nation/otto-workbench/commit/677344b16c40dce99caeee0a5f33ab7679e9c16c))


### Bug Fixes

* **pr-comments:** add --repo-dir flag; improve skill discoverability ([#228](https://github.com/otto-nation/otto-workbench/issues/228)) ([e16530d](https://github.com/otto-nation/otto-workbench/commit/e16530da29fd84173814722bc6ada1075efca780))
* **review:** add missing sys import in review_pipeline ([#234](https://github.com/otto-nation/otto-workbench/issues/234)) ([dc879d5](https://github.com/otto-nation/otto-workbench/commit/dc879d5eafd3ab64b5f1d42b0af58747278ee8d5))


### Code Refactoring

* **auto-tasks:** run dream/promote/retro as headless sessions ([#233](https://github.com/otto-nation/otto-workbench/issues/233)) ([f889f3d](https://github.com/otto-nation/otto-workbench/commit/f889f3d4a8318b38480e6839e36598f9e6f159be))
* **review:** absorb pr-comments-status into claude-review threads ([#232](https://github.com/otto-nation/otto-workbench/issues/232)) ([f23248d](https://github.com/otto-nation/otto-workbench/commit/f23248d756c9356033d8d23efaff416b124894ba))

## [1.20.0](https://github.com/otto-nation/otto-workbench/compare/v1.19.0...v1.20.0) (2026-06-15)


### Features

* **pr-comments:** add thread lifecycle tracking for multi-round reviews ([#226](https://github.com/otto-nation/otto-workbench/issues/226)) ([6b49dc6](https://github.com/otto-nation/otto-workbench/commit/6b49dc6fb2beb0abfd02fd189ba385da481aa17c))

## [1.19.0](https://github.com/otto-nation/otto-workbench/compare/v1.18.2...v1.19.0) (2026-06-12)


### Features

* **retro:** add PR review feedback loop for rules improvement ([#224](https://github.com/otto-nation/otto-workbench/issues/224)) ([40ecb40](https://github.com/otto-nation/otto-workbench/commit/40ecb405e1903eea08b9fd2ad6d59f6215218924))


### Bug Fixes

* **dream,promote:** skip projects without memory/ in trigger checks ([#223](https://github.com/otto-nation/otto-workbench/issues/223)) ([cb45c51](https://github.com/otto-nation/otto-workbench/commit/cb45c51f0b18316e579b74cfa0ea971e2de02b6e))

## [1.18.2](https://github.com/otto-nation/otto-workbench/compare/v1.18.1...v1.18.2) (2026-06-12)


### Bug Fixes

* **review-post:** dedup, orphan cleanup; retry failed groups ([#219](https://github.com/otto-nation/otto-workbench/issues/219)) ([7fc0977](https://github.com/otto-nation/otto-workbench/commit/7fc0977c9a33af4e09e84660606a168115a7ab72))

## [1.18.1](https://github.com/otto-nation/otto-workbench/compare/v1.18.0...v1.18.1) (2026-06-11)


### Bug Fixes

* **claude-review:** fix runtime bugs; add comprehensive test coverage ([#216](https://github.com/otto-nation/otto-workbench/issues/216)) ([080205e](https://github.com/otto-nation/otto-workbench/commit/080205e456540933a4fc359ffbf669a79956b5ee))

## [1.18.0](https://github.com/otto-nation/otto-workbench/compare/v1.17.2...v1.18.0) (2026-06-11)


### Features

* **claude-review:** incremental reviews; modular extraction ([#209](https://github.com/otto-nation/otto-workbench/issues/209)) ([2499a83](https://github.com/otto-nation/otto-workbench/commit/2499a8337e06b5ff71c27fa97b3b3a6699a5866c))
* **git:** add worktrunk pre-switch hook to fetch default branch ([#211](https://github.com/otto-nation/otto-workbench/issues/211)) ([825699a](https://github.com/otto-nation/otto-workbench/commit/825699a450c79b46fbb37c9026622b819423e9c4))


### Bug Fixes

* **dream:** per-project cooldowns; add lint-sweep and --draft flag ([#210](https://github.com/otto-nation/otto-workbench/issues/210)) ([d246939](https://github.com/otto-nation/otto-workbench/commit/d246939199ae9641ca8db93fa2503b3676c9be0e))


### Code Refactoring

* **claude-review:** extract review-post into library modules ([#214](https://github.com/otto-nation/otto-workbench/issues/214)) ([719d9ee](https://github.com/otto-nation/otto-workbench/commit/719d9eec252c6f0553fad281e73caef645c59fe0))

## [1.17.2](https://github.com/otto-nation/otto-workbench/compare/v1.17.1...v1.17.2) (2026-06-10)


### Bug Fixes

* **claude-review:** add turn budget and efficiency constraints to reviewer ([#205](https://github.com/otto-nation/otto-workbench/issues/205)) ([acbc469](https://github.com/otto-nation/otto-workbench/commit/acbc469115e3b054a9b6e1fd95931580f4640f75))
* **claude-review:** tolerate h3/hyphenated severity headers; add severity calibration ([#208](https://github.com/otto-nation/otto-workbench/issues/208)) ([52b93f1](https://github.com/otto-nation/otto-workbench/commit/52b93f156906f8ea38215e075c0ccfa75daca572))

## [1.17.1](https://github.com/otto-nation/otto-workbench/compare/v1.17.0...v1.17.1) (2026-06-09)


### Bug Fixes

* **ci:** merge claude-config packaging into workbench job ([#201](https://github.com/otto-nation/otto-workbench/issues/201)) ([2e72717](https://github.com/otto-nation/otto-workbench/commit/2e727179f50ad25796c6a33550a00b871e6ad846))

## [1.17.0](https://github.com/otto-nation/otto-workbench/compare/v1.16.0...v1.17.0) (2026-06-09)


### Features

* **claude:** add --version/-V to all user-facing scripts ([#200](https://github.com/otto-nation/otto-workbench/issues/200)) ([4c14cd2](https://github.com/otto-nation/otto-workbench/commit/4c14cd24069709fd7188ec72334d8074b3b044fb))


### Bug Fixes

* **claude-review:** preserve recent intermediates during gc ([#198](https://github.com/otto-nation/otto-workbench/issues/198)) ([9eabcc2](https://github.com/otto-nation/otto-workbench/commit/9eabcc23cbeb574406f6c00b7a1ac188a5c7020e))

## [1.16.0](https://github.com/otto-nation/otto-workbench/compare/v1.15.0...v1.16.0) (2026-06-09)


### Features

* **commands:** add SSOT commands framework ([#196](https://github.com/otto-nation/otto-workbench/issues/196)) ([e397a38](https://github.com/otto-nation/otto-workbench/commit/e397a38b8bfed1285ee806a1c369f2b033cfbb96))

## [1.15.0](https://github.com/otto-nation/otto-workbench/compare/v1.14.0...v1.15.0) (2026-06-08)


### Features

* **claude-review:** folder storage, smart recovery, gc ([#192](https://github.com/otto-nation/otto-workbench/issues/192)) ([849f543](https://github.com/otto-nation/otto-workbench/commit/849f543bf3695fd3fcb13adc95bc76608d907b46))

## [1.14.0](https://github.com/otto-nation/otto-workbench/compare/v1.13.1...v1.14.0) (2026-06-08)


### Features

* **claude:** manage additionalDirectories; close permission gaps ([#191](https://github.com/otto-nation/otto-workbench/issues/191)) ([88e6493](https://github.com/otto-nation/otto-workbench/commit/88e649336e820f415d0e50d64802b09dd7a81595))


### Bug Fixes

* **review:** improve review-post resilience for SHA drift and path-less findings ([#188](https://github.com/otto-nation/otto-workbench/issues/188)) ([50563d2](https://github.com/otto-nation/otto-workbench/commit/50563d262f1313dab55077c9f2ae62a033927706))

## [1.13.1](https://github.com/otto-nation/otto-workbench/compare/v1.13.0...v1.13.1) (2026-06-08)


### Bug Fixes

* **ci:** add claude-config-release dispatch to homelab ([#186](https://github.com/otto-nation/otto-workbench/issues/186)) ([3a500e0](https://github.com/otto-nation/otto-workbench/commit/3a500e0b6748d4cd45ca9a4d2ca0d57a7a8c283e))

## [1.13.0](https://github.com/otto-nation/otto-workbench/compare/v1.12.2...v1.13.0) (2026-06-08)


### Features

* **dream:** add dream-scan and dream-verify scripts ([#184](https://github.com/otto-nation/otto-workbench/issues/184)) ([13cf944](https://github.com/otto-nation/otto-workbench/commit/13cf944c5ae0c2fb5d582e9836706c89693e07bb))
* **promote:** add promote-scan script ([#185](https://github.com/otto-nation/otto-workbench/issues/185)) ([4d7659a](https://github.com/otto-nation/otto-workbench/commit/4d7659a501babbee251339da5fa5e18bd17b595c))


### Bug Fixes

* **review:** improve orchestrate resilience for model errors and denied writes ([#183](https://github.com/otto-nation/otto-workbench/issues/183)) ([e4ae310](https://github.com/otto-nation/otto-workbench/commit/e4ae3105631969fdcd2196e1c4fc579980057b33))

## [1.12.2](https://github.com/otto-nation/otto-workbench/compare/v1.12.1...v1.12.2) (2026-06-05)


### Bug Fixes

* **review:** clean empty markers and fix stale verdict counts ([#178](https://github.com/otto-nation/otto-workbench/issues/178)) ([0b74247](https://github.com/otto-nation/otto-workbench/commit/0b7424749d3bd258965fcfaca0e3dd4687f7ded7))

## [1.12.1](https://github.com/otto-nation/otto-workbench/compare/v1.12.0...v1.12.1) (2026-06-05)


### Bug Fixes

* **ci:** retry homebrew deploy on 409 conflict; improve error handling ([#171](https://github.com/otto-nation/otto-workbench/issues/171)) ([37af699](https://github.com/otto-nation/otto-workbench/commit/37af699a63fbb332c56fa8cbcd51c57fc7f5b369))

## [1.12.0](https://github.com/otto-nation/otto-workbench/compare/v1.11.0...v1.12.0) (2026-06-04)


### Features

* **pr:** add --title and --body flags to pr:create and pr:update ([#167](https://github.com/otto-nation/otto-workbench/issues/167)) ([7d8d82c](https://github.com/otto-nation/otto-workbench/commit/7d8d82c2b76b0cb2bf94c3d1f96bff17f28cfae6))
* **review:** add evidence verification, stable IDs, and posted comment dedup ([#166](https://github.com/otto-nation/otto-workbench/issues/166)) ([003e97a](https://github.com/otto-nation/otto-workbench/commit/003e97aa4ab9b2ea99e3d7315ccd23ec83f71e5e))

## [1.11.0](https://github.com/otto-nation/otto-workbench/compare/v1.10.1...v1.11.0) (2026-06-04)


### Features

* **ai:** publish claude-config tarball on releases ([#162](https://github.com/otto-nation/otto-workbench/issues/162)) ([4d39842](https://github.com/otto-nation/otto-workbench/commit/4d39842eef3f8eb15971b29612e60a0153c65b78))

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
