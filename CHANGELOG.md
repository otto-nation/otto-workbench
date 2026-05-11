# Changelog

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
