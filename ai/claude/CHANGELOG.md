# Changelog

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
