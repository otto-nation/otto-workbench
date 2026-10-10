# Changelog

## [2.0.0](https://github.com/otto-nation/otto-workbench/compare/v1.47.0...v2.0.0) (2026-10-10)


### ⚠ BREAKING CHANGES

* **task:** command:task — the task --global wrapper is removed; task on PATH is go-task, brew:dump runs from the repo's own Taskfile, and brew:install is replaced by otto-workbench install
* **ai:** task commit, commit:reword, review, pr:review and ai:setup are removed, AI_COMMAND is no longer read, and the ci-cd agent is deleted; use pr create, pr describe and pr review, and ai/setup.sh to scaffold the GH_TOKEN file
* **pr:** task pr:update is removed; use pr describe [--post] with --title/--body/--closes as needed.
* **review:** the `claude-review` command is now `review`. Invocations of `claude-review` fail once the symlink is removed; use `review` or `pr review`.
* **mcp:** `bin/local/validate-tool-schema` is removed. Nothing invokes it but `validate-all`, which discovers validators by glob.
* **config:** the config key issues.jira_url is now issues.base_url. The machine-wide and container scopes are migrated automatically; a repo's own committed .workbench.yml is only warned about and needs a commit from its owner. An unmigrated key is dropped by the loader, which costs the issue link in reviews rather than raising an error.
* **config:** the config section `issue_tracker` is now `issues`, moving config:issue_tracker and its seven sub-keys. Machines are migrated on the next `otto-workbench sync`; a repo's committed .workbench.yml is reported and must be renamed by hand.
* **ai:** the claude-rules command is renamed workbench-rules; it no longer writes into ~/.claude/rules/, and its local rules now live under the workbench config root's override tree
* **ai:** the review.model, review.thinking, review.provider and review.phases config keys now live under agent.*, and the CLAUDE_REVIEW_MODEL / CLAUDE_REVIEW_THINKING / CLAUDE_REVIEW_<PHASE>_* environment keys are replaced by the WORKBENCH_AI_* keys. A machine's config.yml is rewritten by the 20260824-agent-config-section migration; a project's own .workbench.yml and any exported environment need updating by hand.

### Features

* **agent:** choose the AI backend explicitly, and let Pi review without ~/.claude ([#1422](https://github.com/otto-nation/otto-workbench/issues/1422)) ([296fd18](https://github.com/otto-nation/otto-workbench/commit/296fd1858163bf58c6feda17937d7af6d0244449))
* **agents:** make AGENTS.md the project instructions file ([#1688](https://github.com/otto-nation/otto-workbench/issues/1688)) ([529b70c](https://github.com/otto-nation/otto-workbench/commit/529b70ce5cc973d0603cc6671776a6cad863d0c2))
* **ai:** block sleeping to wait for work that reports itself ([#1283](https://github.com/otto-nation/otto-workbench/issues/1283)) ([e6c26f2](https://github.com/otto-nation/otto-workbench/commit/e6c26f2ff078d0606bc303d5356e52af93514dfa))
* **ai:** canary for the --add-dir rules-loading coupling ([#1387](https://github.com/otto-nation/otto-workbench/issues/1387)) ([110c2de](https://github.com/otto-nation/otto-workbench/commit/110c2de68ef9f5d33cf603a6e00f5227413e9b2a))
* **ai:** carry the workbench's guidelines and agent protocols to Pi ([#1116](https://github.com/otto-nation/otto-workbench/issues/1116)) ([b05a35c](https://github.com/otto-nation/otto-workbench/commit/b05a35cda29e22488d376ee0ce9d8a2a6a095dff))
* **ai:** install one skills tree into both Claude Code and Pi ([#1101](https://github.com/otto-nation/otto-workbench/issues/1101)) ([fb09321](https://github.com/otto-nation/otto-workbench/commit/fb0932199ad694748059817fd06c09060b675ead))
* **ai:** install rtk and worktrunk through mise when Homebrew is absent ([#1664](https://github.com/otto-nation/otto-workbench/issues/1664)) ([2b0e2b4](https://github.com/otto-nation/otto-workbench/commit/2b0e2b4aed416a044d8e4b3906777598555e39e8))
* **ai:** one owner for committing a pass's work and pushing it ([#984](https://github.com/otto-nation/otto-workbench/issues/984)) ([f8b3131](https://github.com/otto-nation/otto-workbench/commit/f8b31317a684438746aab62ca93a82e9c580dd1e)), closes [#904](https://github.com/otto-nation/otto-workbench/issues/904)
* **ai:** read sessions from every harness, not just Claude ([#1347](https://github.com/otto-nation/otto-workbench/issues/1347)) ([4715e5d](https://github.com/otto-nation/otto-workbench/commit/4715e5d337bc93d1594bcafc3bce1300d87f7010))
* **ai:** read the tree-validation lock in both harnesses ([#1523](https://github.com/otto-nation/otto-workbench/issues/1523)) ([34c8d9c](https://github.com/otto-nation/otto-workbench/commit/34c8d9c852e05fb9713b0d28b5f7919600e4e70e))
* **ai:** record a thread the operator settled by hand ([#1009](https://github.com/otto-nation/otto-workbench/issues/1009)) ([02b5099](https://github.com/otto-nation/otto-workbench/commit/02b50992bbaff469b48e6e4e69ff689fec03ee22))
* **ai:** route every prompt-shaped agent call through the one owner ([#993](https://github.com/otto-nation/otto-workbench/issues/993)) ([37eff93](https://github.com/otto-nation/otto-workbench/commit/37eff9394337e7e781ed10ca287f2d9dfb563553))
* **batch:** a pr batch status an agent can act on ([#1691](https://github.com/otto-nation/otto-workbench/issues/1691)) ([1388e4a](https://github.com/otto-nation/otto-workbench/commit/1388e4ace2c062f0f5305e36d6f66a1476e80732))
* **batch:** classify steps from the tree and add CI step and publish ([#1650](https://github.com/otto-nation/otto-workbench/issues/1650)) ([6d6110e](https://github.com/otto-nation/otto-workbench/commit/6d6110e424fa7ef970cb522545b9d0578a90c3c4))
* **batch:** pay closeout debt pr recorded as owed ([#1692](https://github.com/otto-nation/otto-workbench/issues/1692)) ([c9f3202](https://github.com/otto-nation/otto-workbench/commit/c9f3202519cb6ae1ee6ce180113e890a0b21e1f3))
* **ci:** shard bats tests and add change-based selection ([#1159](https://github.com/otto-nation/otto-workbench/issues/1159)) ([4e6efdc](https://github.com/otto-nation/otto-workbench/commit/4e6efdcde35ea66f7a512b8048e49765d16da7f7))
* **claude:** suppress Claude commit/PR attribution via settings ([#1130](https://github.com/otto-nation/otto-workbench/issues/1130)) ([bbe7c56](https://github.com/otto-nation/otto-workbench/commit/bbe7c567d009758636be302b2d3c72f71177ce99))
* **config:** report every scope and read the container's .workbench.yml ([#983](https://github.com/otto-nation/otto-workbench/issues/983)) ([c8dc56a](https://github.com/otto-nation/otto-workbench/commit/c8dc56a74689adee4a3df5e15d692ef6eca71e52))
* **docs:** render tool docs from their owners; root agents in worktrees ([#1609](https://github.com/otto-nation/otto-workbench/issues/1609)) ([7d4e3b5](https://github.com/otto-nation/otto-workbench/commit/7d4e3b5c11f3ebf3fd50e90f2cf8cae1f95ed7b7))
* **eval:** grade the pr-comments publish path ([#1176](https://github.com/otto-nation/otto-workbench/issues/1176)) ([a8dd81c](https://github.com/otto-nation/otto-workbench/commit/a8dd81c822bf09856bd760bc396d5a7b6be5c613))
* **eval:** measure the rule prefix; ratchet the baselines ([#1540](https://github.com/otto-nation/otto-workbench/issues/1540)) ([8c33b58](https://github.com/otto-nation/otto-workbench/commit/8c33b58c7d2b6d4b563841294215a9acbeb03eb4))
* **fix:** make the fixed box ask what test holds the change ([#1358](https://github.com/otto-nation/otto-workbench/issues/1358)) ([0079f91](https://github.com/otto-nation/otto-workbench/commit/0079f91a8e3169559bc7d66f08d1ee213b10d7ae))
* **fix:** point a red suite at the item whose symbol it names ([#1566](https://github.com/otto-nation/otto-workbench/issues/1566)) ([8e6717d](https://github.com/otto-nation/otto-workbench/commit/8e6717de5b8cc63547b68dbf3db221ef692f536f))
* **fix:** run the repo's own checks before a fix pass claims a fix ([#1565](https://github.com/otto-nation/otto-workbench/issues/1565)) ([37ca2d2](https://github.com/otto-nation/otto-workbench/commit/37ca2d291e5d800e88ed7539de16a17a6ffb92d8))
* **git:** verify a push actually landed on the remote ([#972](https://github.com/otto-nation/otto-workbench/issues/972)) ([b95f8ec](https://github.com/otto-nation/otto-workbench/commit/b95f8ec548bd3214f73ba530772c272745cfcb27))
* **git:** verify hand-typed pushes via pre-push intent recording ([#987](https://github.com/otto-nation/otto-workbench/issues/987)) ([c69866f](https://github.com/otto-nation/otto-workbench/commit/c69866f8be79d778775760414aca916487075971))
* **guards:** refuse a test suite piped into a filter ([#1394](https://github.com/otto-nation/otto-workbench/issues/1394)) ([03c579f](https://github.com/otto-nation/otto-workbench/commit/03c579fd7bd3e183238bfcca4eca44e8af62cb10))
* **guards:** refuse issue filing while the branch review has open findings ([#1402](https://github.com/otto-nation/otto-workbench/issues/1402)) ([79e7de0](https://github.com/otto-nation/otto-workbench/commit/79e7de0ac9d2242894e1ad91bb4951e8e3d475e9))
* **herdr:** add herdr AI sub-tool ([#1662](https://github.com/otto-nation/otto-workbench/issues/1662)) ([44b0dab](https://github.com/otto-nation/otto-workbench/commit/44b0dabd959a0ed0f57ec781c0de4e3930b6cd93))
* **issues:** label the issues automation files follow-up ([#1192](https://github.com/otto-nation/otto-workbench/issues/1192)) ([4017e73](https://github.com/otto-nation/otto-workbench/commit/4017e7364ac6cef39d168f1cb99ed08064a45a4a))
* **lock:** declare tree validation with a lock both harnesses read ([#1316](https://github.com/otto-nation/otto-workbench/issues/1316)) ([155eb6c](https://github.com/otto-nation/otto-workbench/commit/155eb6cc063d866eaecf5c86f5ac383f23b8c19a))
* **maintenance:** fire the four auto-task gates from the maintenance timer ([#1413](https://github.com/otto-nation/otto-workbench/issues/1413)) ([7fbcf92](https://github.com/otto-nation/otto-workbench/commit/7fbcf92b2a0a31e339dba43273e6e442f0bf64ef))
* **migrations:** run a migration once per repo, not per worktree ([#1049](https://github.com/otto-nation/otto-workbench/issues/1049)) ([8a1060a](https://github.com/otto-nation/otto-workbench/commit/8a1060a38cae584b155ef651ff174444cbb067c7))
* **permissions:** keep a tracked allow bucket in codepoint order ([#1029](https://github.com/otto-nation/otto-workbench/issues/1029)) ([16a579d](https://github.com/otto-nation/otto-workbench/commit/16a579d93b47dd9ccd2ceea1e3f806df42c4d112))
* **permissions:** mirror tracked grants into the bare-repo container ([#946](https://github.com/otto-nation/otto-workbench/issues/946)) ([e2c581d](https://github.com/otto-nation/otto-workbench/commit/e2c581d608f96245e3c373f49a76abe8209f1ac4))
* **permissions:** sweep local grant drift across registered repos ([#935](https://github.com/otto-nation/otto-workbench/issues/935)) ([507e20d](https://github.com/otto-nation/otto-workbench/commit/507e20dfaae5e2e7f0fa64f13a66de2af0967927))
* **pi:** catch and cure extension clones the installed pi cannot serve ([#1558](https://github.com/otto-nation/otto-workbench/issues/1558)) ([69163d1](https://github.com/otto-nation/otto-workbench/commit/69163d1e61f5bdd050353519de8f10f66e3d5d48))
* **pi:** install Pi extensions from ai/pi/extensions ([#1190](https://github.com/otto-nation/otto-workbench/issues/1190)) ([84e4c62](https://github.com/otto-nation/otto-workbench/commit/84e4c620de9f5b302ee8029bab2dca6afad440d1))
* **pi:** install Pi with its own installer during AI setup ([#1094](https://github.com/otto-nation/otto-workbench/issues/1094)) ([a629b2e](https://github.com/otto-nation/otto-workbench/commit/a629b2e8e4eab25d719640c703112fad46b08d99))
* **pi:** install pi-extensions; write settings where Pi reads them ([#1089](https://github.com/otto-nation/otto-workbench/issues/1089)) ([b7ae08d](https://github.com/otto-nation/otto-workbench/commit/b7ae08d59e2505d38a7f62602b942ea3738e5089))
* **pi:** install superpowers with worktree and branch-completion shims ([#1265](https://github.com/otto-nation/otto-workbench/issues/1265)) ([7d246f1](https://github.com/otto-nation/otto-workbench/commit/7d246f1ab0404969a9e6a25e449b4ea5e32195db))
* **pi:** refuse a command whose trailing echo discards the suite status ([#1419](https://github.com/otto-nation/otto-workbench/issues/1419)) ([906ac7e](https://github.com/otto-nation/otto-workbench/commit/906ac7e20ad33d7dd6b048fc84193d4f87123754))
* **pi:** route Superpowers model tiers through the subagent tool ([#1680](https://github.com/otto-nation/otto-workbench/issues/1680)) ([08088a4](https://github.com/otto-nation/otto-workbench/commit/08088a48cc536e1d4b713440abda9497fa09ce8d))
* **pi:** update the host on sync and gate it against declared floors ([#1563](https://github.com/otto-nation/otto-workbench/issues/1563)) ([fa08e7b](https://github.com/otto-nation/otto-workbench/commit/fa08e7bfc2866376b434f21c52f7d88af5d167b6))
* **pi:** warn on unknown AI model ids during sync ([#1577](https://github.com/otto-nation/otto-workbench/issues/1577)) ([8f636a0](https://github.com/otto-nation/otto-workbench/commit/8f636a04c8c82b198784b45e91a83834c2161e25))
* **pr-comments:** verify agent fixes before reporting them as fixed ([#1298](https://github.com/otto-nation/otto-workbench/issues/1298)) ([3785efc](https://github.com/otto-nation/otto-workbench/commit/3785efc0b073a3737c32d5cbe7507748d35dffec))
* **pr-rebase:** rebuild generated files instead of hand-editing them ([#1148](https://github.com/otto-nation/otto-workbench/issues/1148)) ([01254d6](https://github.com/otto-nation/otto-workbench/commit/01254d6b8e8aaa8a386b8d0ec13b622d740281f8))
* **pr:** add --closes to link an issue for auto-close ([#1278](https://github.com/otto-nation/otto-workbench/issues/1278)) ([de60820](https://github.com/otto-nation/otto-workbench/commit/de608205e26dde9acd3602cf3381681af117221e))
* **pr:** add --issue flag to pr:create for direct issue linking ([#1156](https://github.com/otto-nation/otto-workbench/issues/1156)) ([febf6b5](https://github.com/otto-nation/otto-workbench/commit/febf6b55f436eb0d649b6015de28e0711b3fff32))
* **pr:** add --push flag to pr review --self --fix ([#1155](https://github.com/otto-nation/otto-workbench/issues/1155)) ([a59419d](https://github.com/otto-nation/otto-workbench/commit/a59419d0500fef4276eb5d24c580ae4e0262778f))
* **pr:** add pr batch to run fixes across all my open PRs ([#1593](https://github.com/otto-nation/otto-workbench/issues/1593)) ([30011e1](https://github.com/otto-nation/otto-workbench/commit/30011e13018c87ce04657965d7a2576ff8c76284))
* **pr:** key a run target by the forge instance its repo declares ([#1539](https://github.com/otto-nation/otto-workbench/issues/1539)) ([3cd5513](https://github.com/otto-nation/otto-workbench/commit/3cd551392c42d7585ffd6829fd5a0b86ee932de7))
* **pr:** let pr:create push past a gate failure it has already read ([#1547](https://github.com/otto-nation/otto-workbench/issues/1547)) ([bde71a8](https://github.com/otto-nation/otto-workbench/commit/bde71a83d4cae1b81165208214d0b1c8d93e283a))
* **projects:** group a repo's worktrees under the repo ([#1061](https://github.com/otto-nation/otto-workbench/issues/1061)) ([6dec134](https://github.com/otto-nation/otto-workbench/commit/6dec1349f551d1375e45e0691847dc405f96abd5))
* **pr:** pr describe absorbs task pr:update; delete lib/ai/pr.sh ([#1654](https://github.com/otto-nation/otto-workbench/issues/1654)) ([0bc25e4](https://github.com/otto-nation/otto-workbench/commit/0bc25e4b7a8d8bbc2de562e7407a3f8e5e66ea29))
* **pr:** render PR links on the forge the repo is served from ([#1437](https://github.com/otto-nation/otto-workbench/issues/1437)) ([de0884b](https://github.com/otto-nation/otto-workbench/commit/de0884b4aca04e75675d67679cda5fb19e5e5eaf))
* **rebase:** refuse resolutions that discard cleanly merged changes ([#1580](https://github.com/otto-nation/otto-workbench/issues/1580)) ([f1a0849](https://github.com/otto-nation/otto-workbench/commit/f1a08496a9028f9730635864fb1a0a275e615b24))
* **rebase:** reuse recorded resolutions; fold fixup commits ([#1352](https://github.com/otto-nation/otto-workbench/issues/1352)) ([87ccd22](https://github.com/otto-nation/otto-workbench/commit/87ccd22bb9a766a18f4736f3915acbcbe6df6ae5))
* **retro:** archive each report before the next scan overwrites it ([#1505](https://github.com/otto-nation/otto-workbench/issues/1505)) ([93e3ccf](https://github.com/otto-nation/otto-workbench/commit/93e3ccf0eb521db015a82927d8c29d94a36063c8))
* **review:** close out the review pipeline sweep ([#1544](https://github.com/otto-nation/otto-workbench/issues/1544)) ([96b6d19](https://github.com/otto-nation/otto-workbench/commit/96b6d198219f36be8287efc4390363fff58ddd00))
* **review:** let the fix pass fix static analysis violations ([#1541](https://github.com/otto-nation/otto-workbench/issues/1541)) ([0832e4e](https://github.com/otto-nation/otto-workbench/commit/0832e4ec0171510049bacdfbd4eca8b2eb5ca89f))
* **review:** measure a prompt's real token cost ([#1218](https://github.com/otto-nation/otto-workbench/issues/1218)) ([e07b973](https://github.com/otto-nation/otto-workbench/commit/e07b9730de9f1b53b766d4697c8fb55740e4fe08))
* **review:** read context windows from pi's catalogue ([#1576](https://github.com/otto-nation/otto-workbench/issues/1576)) ([41d25e6](https://github.com/otto-nation/otto-workbench/commit/41d25e6cfb0f9c447d235ddd587bf97993575985))
* **review:** render every URL on the forge its repo is served from ([#1449](https://github.com/otto-nation/otto-workbench/issues/1449)) ([edbcf43](https://github.com/otto-nation/otto-workbench/commit/edbcf43050f5602656ec32cb8e4656a4f183ee11)), closes [#1440](https://github.com/otto-nation/otto-workbench/issues/1440) [#1445](https://github.com/otto-nation/otto-workbench/issues/1445)
* **review:** size a re-review by its delta, and skip a no-op one ([#1279](https://github.com/otto-nation/otto-workbench/issues/1279)) ([80bd23f](https://github.com/otto-nation/otto-workbench/commit/80bd23f096d41661322aea5715cb9e04a41f6295))
* **self-review:** audit what a fix pass changed, and close the three gaps behind it ([#1458](https://github.com/otto-nation/otto-workbench/issues/1458)) ([0fec8fd](https://github.com/otto-nation/otto-workbench/commit/0fec8fd804e9a237a2e574971b4cf7a33ee4b20c))
* **skills:** add writing-skills skill; retire skills-authoring rule ([#1271](https://github.com/otto-nation/otto-workbench/issues/1271)) ([22f6cf1](https://github.com/otto-nation/otto-workbench/commit/22f6cf184872dfdd0feaf06975f2a665c1029cf6))
* SSOT model config across Claude Code and Pi ([#1157](https://github.com/otto-nation/otto-workbench/issues/1157)) ([6237430](https://github.com/otto-nation/otto-workbench/commit/623743022f0e0a02ed490a173dcbb414bb1e67a2))
* **task:** add a named target for the full pre-push gate ([#1378](https://github.com/otto-nation/otto-workbench/issues/1378)) ([6cbb961](https://github.com/otto-nation/otto-workbench/commit/6cbb961cc8adf084d76f2409ec3bb5904aec90d8))
* **test:** add transitive dependency closure to select-tests ([#1160](https://github.com/otto-nation/otto-workbench/issues/1160)) ([154c990](https://github.com/otto-nation/otto-workbench/commit/154c99061b8a7bc5c121d66965d2040bdc50aed6))
* **tests:** fail a change whose new test cannot fail ([#1410](https://github.com/otto-nation/otto-workbench/issues/1410)) ([ea47cfa](https://github.com/otto-nation/otto-workbench/commit/ea47cfaddf64d7f7ea82530f5713c737f7cb5a75))
* **tests:** fail a platform skip that silences a test about a tracked file ([#1415](https://github.com/otto-nation/otto-workbench/issues/1415)) ([adaf9ff](https://github.com/otto-nation/otto-workbench/commit/adaf9ff03fab3fa17ced813d9f4e6b90a941591c))
* **tests:** gate test module names and size with validate-test-layout ([#1586](https://github.com/otto-nation/otto-workbench/issues/1586)) ([6614ba4](https://github.com/otto-nation/otto-workbench/commit/6614ba4c69b714dcd370e258e2e31a89a1da5938))
* **trail:** correlate one user command across the processes it spawns ([#1309](https://github.com/otto-nation/otto-workbench/issues/1309)) ([8aa110c](https://github.com/otto-nation/otto-workbench/commit/8aa110ceb7283f978a06764a2fe6be6622401657))
* **trail:** record the dream and retro runs, not just their scans ([#1360](https://github.com/otto-nation/otto-workbench/issues/1360)) ([caa5ab7](https://github.com/otto-nation/otto-workbench/commit/caa5ab7b80aae122854a716995615866b61dfe95))
* **trail:** record who started a run; note a shared worktree ([#1459](https://github.com/otto-nation/otto-workbench/issues/1459)) ([d2c53a7](https://github.com/otto-nation/otto-workbench/commit/d2c53a71c06b20402c7845b2974e6a75f908e385))
* **validate:** cap bats suites at the test-layout limit ([#1611](https://github.com/otto-nation/otto-workbench/issues/1611)) ([ed9a204](https://github.com/otto-nation/otto-workbench/commit/ed9a20498671e270d9be949b0e75c6ca5d058666)), closes [#853](https://github.com/otto-nation/otto-workbench/issues/853)
* **validate:** cap source files on code lines, not total lines ([#1479](https://github.com/otto-nation/otto-workbench/issues/1479)) ([314b047](https://github.com/otto-nation/otto-workbench/commit/314b047717492eb0276622132f3031dc4e304dfb))
* **validate:** detect superpowers shims drifting from the pinned version ([#1285](https://github.com/otto-nation/otto-workbench/issues/1285)) ([1e537b8](https://github.com/otto-nation/otto-workbench/commit/1e537b82b9a3b601c91a7a0692105b179eff00ec))
* **validate:** fail an ai/lib import its layer does not permit ([#1145](https://github.com/otto-nation/otto-workbench/issues/1145)) ([02f5be5](https://github.com/otto-nation/otto-workbench/commit/02f5be5c403299c753f70197cbf582ec688b50f9))
* **validate:** hold every ai/lib entry point to the shim shape ([#1657](https://github.com/otto-nation/otto-workbench/issues/1657)) ([e83658f](https://github.com/otto-nation/otto-workbench/commit/e83658fe4991e4e805ce12a1e05f73f6cd7fa44a)), closes [#911](https://github.com/otto-nation/otto-workbench/issues/911)
* **validate:** make the size gates unconditional ([#1649](https://github.com/otto-nation/otto-workbench/issues/1649)) ([c91b6f5](https://github.com/otto-nation/otto-workbench/commit/c91b6f5a8c8d5926e1ba386e4c541b32de798976))
* **validate:** refuse subpackages under ai/lib ([#1645](https://github.com/otto-nation/otto-workbench/issues/1645)) ([494cf18](https://github.com/otto-nation/otto-workbench/commit/494cf18ce323b86679e756940ac2be48a8bd4836)), closes [#911](https://github.com/otto-nation/otto-workbench/issues/911)
* **wiki:** add archive and signals subcommands ([#1233](https://github.com/otto-nation/otto-workbench/issues/1233)) ([ef28a40](https://github.com/otto-nation/otto-workbench/commit/ef28a409c2a873cb43e740252ddb3ec552d3d7c8))
* **wiki:** add init and ingest, and split the store into ai/lib/wiki ([#1178](https://github.com/otto-nation/otto-workbench/issues/1178)) ([72a2e3f](https://github.com/otto-nation/otto-workbench/commit/72a2e3f049cf90e31f87a2c19af5f19dc41ea466))
* **wiki:** compile knowledge bases from a shared skill and CLI ([#1172](https://github.com/otto-nation/otto-workbench/issues/1172)) ([bd673d2](https://github.com/otto-nation/otto-workbench/commit/bd673d23f87c66eaeaebb3b91c5d70f8b8172f65))
* **wiki:** keep knowledge bases in a machine-level vault, not a worktree ([#1514](https://github.com/otto-nation/otto-workbench/issues/1514)) ([faf4642](https://github.com/otto-nation/otto-workbench/commit/faf4642c665daa0a09c8d48509e4297b34e2c9e2))
* **wiki:** make the knowledge base directory configurable ([#1173](https://github.com/otto-nation/otto-workbench/issues/1173)) ([f90376c](https://github.com/otto-nation/otto-workbench/commit/f90376c1d87175edfb4196feee5e60baaa7ac3a8))
* **wiki:** passive knowledge capture at session end ([#1191](https://github.com/otto-nation/otto-workbench/issues/1191)) ([c4f9b2e](https://github.com/otto-nation/otto-workbench/commit/c4f9b2ee0303f8c15eacd225fe78f957c037c75a))
* **workbench:** draw down the follow-up backlog ([#1548](https://github.com/otto-nation/otto-workbench/issues/1548)) ([cca0570](https://github.com/otto-nation/otto-workbench/commit/cca057031bfbd1e80bcad36a687eaabe130efd18))
* **workspace:** keep plans and specs at the container ([#1564](https://github.com/otto-nation/otto-workbench/issues/1564)) ([0f2db4b](https://github.com/otto-nation/otto-workbench/commit/0f2db4b6b9fba9a32431b37c2ecdd807dba18422))
* **zsh:** launch claude in a worktree, not the bare container ([#961](https://github.com/otto-nation/otto-workbench/issues/961)) ([af0f215](https://github.com/otto-nation/otto-workbench/commit/af0f215ef5acc57e818b518aec322302c56f188d))


### Bug Fixes

* **agent:** end a Pi run on a failed RPC response instead of hanging ([#1448](https://github.com/otto-nation/otto-workbench/issues/1448)) ([a56a790](https://github.com/otto-nation/otto-workbench/commit/a56a790c2d8d53d4d0c5967290c03469c73e6f61))
* **agent:** fail the run when the Pi backend refuses a prompt ([#1436](https://github.com/otto-nation/otto-workbench/issues/1436)) ([39e4122](https://github.com/otto-nation/otto-workbench/commit/39e41229d4710cbee92972428ad8b21a35535586))
* **agent:** give a Pi prompt the same bare flags as an agent run ([#1557](https://github.com/otto-nation/otto-workbench/issues/1557)) ([a5566e6](https://github.com/otto-nation/otto-workbench/commit/a5566e651bf5a6fdd314cc5ff431e4bf67512f28))
* **agent:** grow a retried fix pass; rank density below tier in preflight ([#1439](https://github.com/otto-nation/otto-workbench/issues/1439)) ([b69eebc](https://github.com/otto-nation/otto-workbench/commit/b69eebc9e267bd6b1d5d2d87d45e5c9d5205e60b))
* **agent:** measure progress against the output file, not any write ([#1501](https://github.com/otto-nation/otto-workbench/issues/1501)) ([eac768a](https://github.com/otto-nation/otto-workbench/commit/eac768ac92cc534fcf582f7bc769511ea2e7040a))
* **agent:** name the write tool the selected backend actually has ([#1430](https://github.com/otto-nation/otto-workbench/issues/1430)) ([dc7df6b](https://github.com/otto-nation/otto-workbench/commit/dc7df6b6fcda06f4281e0e04ce8bc3b6e88ae3e5))
* **agent:** raise and account for the fix-pass turn budget ([#1498](https://github.com/otto-nation/otto-workbench/issues/1498)) ([27159b0](https://github.com/otto-nation/otto-workbench/commit/27159b0fe0e318b81bb7800cfe656c1b38733d64))
* **agent:** report a truncated fix pass as truncated ([#1492](https://github.com/otto-nation/otto-workbench/issues/1492)) ([277fb8e](https://github.com/otto-nation/otto-workbench/commit/277fb8ee9fed08b6f2749d4bb409e99aa40418fd))
* **agent:** resolve tier aliases via AI_*_MODEL names ([#1432](https://github.com/otto-nation/otto-workbench/issues/1432)) ([e6be9b0](https://github.com/otto-nation/otto-workbench/commit/e6be9b045e33cf124e62f0768b1226f902701c3d))
* **agent:** salvage a review the agent narrated instead of writing ([#1555](https://github.com/otto-nation/otto-workbench/issues/1555)) ([f4ea202](https://github.com/otto-nation/otto-workbench/commit/f4ea202396af05c2f32f87b999e5dec69d7fbaf6))
* **agent:** share quota backoff across review processes ([#1529](https://github.com/otto-nation/otto-workbench/issues/1529)) ([295de4c](https://github.com/otto-nation/otto-workbench/commit/295de4c3cacfbf237e7222719ef9c3016ba344d4))
* **agent:** stop a stateless prompt holding a shell, and pin agents' editors ([#1460](https://github.com/otto-nation/otto-workbench/issues/1460)) ([24ad75e](https://github.com/otto-nation/otto-workbench/commit/24ad75e42fb9c142385c0f1aa2f1cdb000f2317d))
* **agent:** strip the interactive preamble from Pi agents ([#1470](https://github.com/otto-nation/otto-workbench/issues/1470)) ([221ae6b](https://github.com/otto-nation/otto-workbench/commit/221ae6bb6982778e254f0f35d53360e5afc6f22f))
* **ai:** a review that skipped synthesis writes a summary section ([#1051](https://github.com/otto-nation/otto-workbench/issues/1051)) ([303eb78](https://github.com/otto-nation/otto-workbench/commit/303eb78ce9303a36d46521e973fe603bb8bad5fc))
* **ai:** a run that crashes in the disprove gate reports itself complete ([#1027](https://github.com/otto-nation/otto-workbench/issues/1027)) ([1926a10](https://github.com/otto-nation/otto-workbench/commit/1926a10ed75ea745fad4b92f753e1a8aaccb8d84))
* **ai:** carry cross-severity finding references through the merge ([#1071](https://github.com/otto-nation/otto-workbench/issues/1071)) ([506f0df](https://github.com/otto-nation/otto-workbench/commit/506f0df64aaf47735e4beab80d052129d781822f))
* **ai:** compose a clean review through the one body builder ([#1093](https://github.com/otto-nation/otto-workbench/issues/1093)) ([6ad230d](https://github.com/otto-nation/otto-workbench/commit/6ad230d8da7401837b3580161c83b6a70245ebbf))
* **ai:** decide a dangling reference in the group that wrote it ([#1083](https://github.com/otto-nation/otto-workbench/issues/1083)) ([56769b1](https://github.com/otto-nation/otto-workbench/commit/56769b1bb840748699d5a511815fa7d67899f388))
* **ai:** finish an unpushed pr-rebase run through the owner ([#998](https://github.com/otto-nation/otto-workbench/issues/998)) ([e5383b3](https://github.com/otto-nation/otto-workbench/commit/e5383b3ad7e034f9c1c9b70e83aa653c9cba1fea))
* **ai:** fix passes rebuild the artifacts their edits invalidate ([#1069](https://github.com/otto-nation/otto-workbench/issues/1069)) ([f72e3d3](https://github.com/otto-nation/otto-workbench/commit/f72e3d3d6a96a3e80be1274537c1dd83b02eea6b))
* **ai:** give the rule layers a home no harness owns ([#1133](https://github.com/otto-nation/otto-workbench/issues/1133)) ([a9b22a7](https://github.com/otto-nation/otto-workbench/commit/a9b22a72f4d718fdd964ac8c774ccddfd3a0185a))
* **ai:** harden validator and test scope ([#1139](https://github.com/otto-nation/otto-workbench/issues/1139), [#1142](https://github.com/otto-nation/otto-workbench/issues/1142), [#1143](https://github.com/otto-nation/otto-workbench/issues/1143), [#1138](https://github.com/otto-nation/otto-workbench/issues/1138), [#1144](https://github.com/otto-nation/otto-workbench/issues/1144)) ([#1151](https://github.com/otto-nation/otto-workbench/issues/1151)) ([d3cc5dd](https://github.com/otto-nation/otto-workbench/commit/d3cc5dd6b3e258ada4750e83ca9be98ddb44abc3))
* **ai:** install the harness-neutral CLIs for every harness ([#1127](https://github.com/otto-nation/otto-workbench/issues/1127)) ([63fc6df](https://github.com/otto-nation/otto-workbench/commit/63fc6dfeaa823e223c3e5bfad49e46a234e4842a))
* **ai:** keep the failing end of hook output ([#1150](https://github.com/otto-nation/otto-workbench/issues/1150)) ([aee937a](https://github.com/otto-nation/otto-workbench/commit/aee937adc51814a7d9ab7073fccae3a5155ff1c0))
* **ai:** let push.py resolve its own siblings on sys.path ([#1348](https://github.com/otto-nation/otto-workbench/issues/1348)) ([b0d9d62](https://github.com/otto-nation/otto-workbench/commit/b0d9d62876e983f3c9931fea066e4747cb41f778))
* **ai:** make ~/.env.local the one owner of the Vertex routing vars ([#1120](https://github.com/otto-nation/otto-workbench/issues/1120)) ([3f25472](https://github.com/otto-nation/otto-workbench/commit/3f2547297ec3be8b09dbd9151649a1a0f0be1d83))
* **ai:** match a reply thread on the finding's identity, not its number ([#1114](https://github.com/otto-nation/otto-workbench/issues/1114)) ([971bdf6](https://github.com/otto-nation/otto-workbench/commit/971bdf609fbb7b2737dc8f0706071a37da3c36b1)), closes [#1108](https://github.com/otto-nation/otto-workbench/issues/1108)
* **ai:** one answer for where a finding's body ends ([#1078](https://github.com/otto-nation/otto-workbench/issues/1078)) ([fc4f032](https://github.com/otto-nation/otto-workbench/commit/fc4f032f9f07cbb7f33ec9330cb3ff4a253e421e))
* **ai:** one owner for the posted finding tag; the annotation was dead ([#1111](https://github.com/otto-nation/otto-workbench/issues/1111)) ([9fefbbd](https://github.com/otto-nation/otto-workbench/commit/9fefbbd0eef41e50c85ae7c03f1833652463aad7))
* **ai:** own a skill symlink by its shape, not by today's layer root ([#1102](https://github.com/otto-nation/otto-workbench/issues/1102)) ([67436d3](https://github.com/otto-nation/otto-workbench/commit/67436d3405a21d776ceaa19d34f845c621b7b0e6))
* **ai:** reach a domain's own reconstruction from apply_state_update ([#1006](https://github.com/otto-nation/otto-workbench/issues/1006)) ([7c4787d](https://github.com/otto-nation/otto-workbench/commit/7c4787d1983b58eddafef66cf2c2ec902ed999be))
* **ai:** regenerate git and tool rules on every sync ([#1648](https://github.com/otto-nation/otto-workbench/issues/1648)) ([bbb7736](https://github.com/otto-nation/otto-workbench/commit/bbb7736d5bc53475790172766ecbb8de9219ab73))
* **ai:** resolve ai/lib and the threads state outside the target tree ([#1333](https://github.com/otto-nation/otto-workbench/issues/1333)) ([d979f9e](https://github.com/otto-nation/otto-workbench/commit/d979f9ed96aa5ab7816e25e0a698dff7498c0c60))
* **ai:** resolve the container to its worktree in SessionStart context ([#1189](https://github.com/otto-nation/otto-workbench/issues/1189)) ([d2798ab](https://github.com/otto-nation/otto-workbench/commit/d2798ab1a163da0c7aa613cb983ee28fcf84f778))
* **ai:** resolve the worktree before writing a project artifact ([#973](https://github.com/otto-nation/otto-workbench/issues/973)) ([3a8eede](https://github.com/otto-nation/otto-workbench/commit/3a8eede04baa692569c037133935ed5e4c81e8ac))
* **ai:** resolve WORKBENCH_ROOT through the Taskfile symlink ([#981](https://github.com/otto-nation/otto-workbench/issues/981)) ([e5828b9](https://github.com/otto-nation/otto-workbench/commit/e5828b9e5080900a09558d3a088d653f32750eb6))
* **ai:** stop review-threads restating every open thread each round ([#1021](https://github.com/otto-nation/otto-workbench/issues/1021)) ([a1d6cce](https://github.com/otto-nation/otto-workbench/commit/a1d6cce8068b8ba071310ea2a3298164b695d128))
* **ai:** tell the fix agent its role; pin the context it already gets ([#1363](https://github.com/otto-nation/otto-workbench/issues/1363)) ([6568712](https://github.com/otto-nation/otto-workbench/commit/6568712adaff1c1c179194be915ad31fdfa8ccd4))
* **auto-task:** drop --bare so skills resolve; gate cascade on a sentinel ([#1400](https://github.com/otto-nation/otto-workbench/issues/1400)) ([8390f3d](https://github.com/otto-nation/otto-workbench/commit/8390f3d137cc1b020696601da3bd7b6437201dae))
* **backend:** measure Pi runs — token counts, prompt usage, model attribution ([#1404](https://github.com/otto-nation/otto-workbench/issues/1404)) ([279ea22](https://github.com/otto-nation/otto-workbench/commit/279ea221a0dc7bc7a10fb7531b55689b6c256172))
* **batch:** judge review freshness against the local worktree HEAD ([#1608](https://github.com/otto-nation/otto-workbench/issues/1608)) ([5c30cfa](https://github.com/otto-nation/otto-workbench/commit/5c30cfa8842fb363916ef08a94ab80ebd8c5e94d))
* **batch:** survive EPERM when killing an exited step's group ([#1618](https://github.com/otto-nation/otto-workbench/issues/1618)) ([f38123b](https://github.com/otto-nation/otto-workbench/commit/f38123b53f7aacb6f86728a1c57c8b46dcfe87a5))
* **bin:** derive repo root from script path instead of leaking bin/local's git dir ([#1282](https://github.com/otto-nation/otto-workbench/issues/1282)) ([03ab81f](https://github.com/otto-nation/otto-workbench/commit/03ab81f7df6b3ee9c1a40302dbfb25cba964466d))
* **ci:** anchor bats failures on the line the log reported ([#1076](https://github.com/otto-nation/otto-workbench/issues/1076)) ([c43d1b2](https://github.com/otto-nation/otto-workbench/commit/c43d1b2954774f189e3795252bccebd4a1d19b5f))
* **ci:** locate a pytest failure at the frame that raised it ([#1128](https://github.com/otto-nation/otto-workbench/issues/1128)) ([32bd682](https://github.com/otto-nation/otto-workbench/commit/32bd6827351cfc3ea10d52c933c0c0317280cef8))
* **ci:** report every check on a commit, and never guess at the rest ([#1568](https://github.com/otto-nation/otto-workbench/issues/1568)) ([251e76a](https://github.com/otto-nation/otto-workbench/commit/251e76a78db31baedea26c761a33bc5db565afb5))
* **claude:** install Claude Code with its own installer, not the cask ([#1088](https://github.com/otto-nation/otto-workbench/issues/1088)) ([b465bc2](https://github.com/otto-nation/otto-workbench/commit/b465bc2cac5572851d303b77a7d80204aea08de0))
* **claude:** keep the settings manifest out of the validated file ([#1098](https://github.com/otto-nation/otto-workbench/issues/1098)) ([99a04a0](https://github.com/otto-nation/otto-workbench/commit/99a04a0e3ad889003f1822d9a6c778ce7c8b276a))
* **claude:** keep the settings sandbox inside its own roots ([#1099](https://github.com/otto-nation/otto-workbench/issues/1099)) ([db91ad3](https://github.com/otto-nation/otto-workbench/commit/db91ad3bb9cc5c7d104533990cb169be95b6b18e))
* **cleanup:** keep the branch when the tracker refused to answer ([#1412](https://github.com/otto-nation/otto-workbench/issues/1412)) ([2e20380](https://github.com/otto-nation/otto-workbench/commit/2e20380a0d6f4876936d5d599f737e7ccbbda3d7))
* **cleanup:** scope PR state lookup to the branches asked about ([#1403](https://github.com/otto-nation/otto-workbench/issues/1403)) ([5bb9313](https://github.com/otto-nation/otto-workbench/commit/5bb93138ce4cc53861695951a9158f68387af2c2))
* **comments:** fold carried-over rows by typed location ([#1254](https://github.com/otto-nation/otto-workbench/issues/1254)) ([0873aee](https://github.com/otto-nation/otto-workbench/commit/0873aee7d55adb1e3e58cc49cf4f43503a26be5d))
* **config:** refuse a write under a key the workbench cannot read ([#965](https://github.com/otto-nation/otto-workbench/issues/965)) ([21ccf03](https://github.com/otto-nation/otto-workbench/commit/21ccf039027d9d602e2014e1956e14913b752be6))
* **core:** close the signal-forwarding race in both lock wrappers ([#1535](https://github.com/otto-nation/otto-workbench/issues/1535)) ([d9acc91](https://github.com/otto-nation/otto-workbench/commit/d9acc9188dc31e005d14c672fb2464807269b623)), closes [#1533](https://github.com/otto-nation/otto-workbench/issues/1533)
* **describe:** gate the PR body edit like every other GitHub write ([#1392](https://github.com/otto-nation/otto-workbench/issues/1392)) ([7998945](https://github.com/otto-nation/otto-workbench/commit/7998945e397377a09c9322500707b9f530bee816))
* **dev:** declare development dependencies once; never skip tests for them ([#1687](https://github.com/otto-nation/otto-workbench/issues/1687)) ([b26bde0](https://github.com/otto-nation/otto-workbench/commit/b26bde069b1dae4526c24b114dc6e74673b00687))
* **dev:** find dev-deps' repo root by path, not git, under the hook ([#1690](https://github.com/otto-nation/otto-workbench/issues/1690)) ([271b708](https://github.com/otto-nation/otto-workbench/commit/271b7080a932b58c5b552fd376b8202b59f37f50))
* **docs:** sort generated skill, agent, MCP, and component lists ([#1244](https://github.com/otto-nation/otto-workbench/issues/1244)) ([c48eb61](https://github.com/otto-nation/otto-workbench/commit/c48eb6117de786d468659abd78dfafcdb41001dd))
* **eval:** a failed invocation is no longer recorded as a genuine zero ([#1163](https://github.com/otto-nation/otto-workbench/issues/1163)) ([423963b](https://github.com/otto-nation/otto-workbench/commit/423963b5049d89d6aaec234ac2725e943b02ee7a))
* **eval:** keep the regression-test fixture out of the tracked corpus ([#1538](https://github.com/otto-nation/otto-workbench/issues/1538)) ([e3099f3](https://github.com/otto-nation/otto-workbench/commit/e3099f388267cfe7e98c63e7327e0ed991567a86))
* **eval:** let a fixture cite the commit it is standing on ([#1181](https://github.com/otto-nation/otto-workbench/issues/1181)) ([094b76f](https://github.com/otto-nation/otto-workbench/commit/094b76fcb6c37b8f37ac6a675ba7938c6e5af377))
* **eval:** point the fixture's origin/HEAD at the branch it forked from ([#1166](https://github.com/otto-nation/otto-workbench/issues/1166)) ([22641ed](https://github.com/otto-nation/otto-workbench/commit/22641ed2667a72691f037f515c69f4719c30498c))
* **fix:** gate the review fix pass; make it disprove before it edits ([#1373](https://github.com/otto-nation/otto-workbench/issues/1373)) ([4ea4b60](https://github.com/otto-nation/otto-workbench/commit/4ea4b601c4360768314f446b4db97d07d0e52321))
* **fix:** reconcile a pass's boxes against the tree it changed ([#1447](https://github.com/otto-nation/otto-workbench/issues/1447)) ([409f757](https://github.com/otto-nation/otto-workbench/commit/409f75772d66f2122074ad8583180c2f9bb7aa74))
* **fix:** scope a fix pass to the branch's own files ([#1493](https://github.com/otto-nation/otto-workbench/issues/1493)) ([2e4ae4a](https://github.com/otto-nation/otto-workbench/commit/2e4ae4a8c4f9c311564c28b9f9770dce8a8a8b9e))
* **fix:** scope fix artifacts and commits to paths the pass owns ([#1261](https://github.com/otto-nation/otto-workbench/issues/1261)) ([80fb6f6](https://github.com/otto-nation/otto-workbench/commit/80fb6f6a971d3a1ec333f070a8a20da61dd5d0f5))
* **gc:** prune target dirs holding a pr-rebase subdirectory ([#1313](https://github.com/otto-nation/otto-workbench/issues/1313)) ([3523722](https://github.com/otto-nation/otto-workbench/commit/35237222fdc8cef307928a6b3090099aebdc552f))
* **gc:** reclaim self-review directories once their branch is finished ([#1520](https://github.com/otto-nation/otto-workbench/issues/1520)) ([fa27e35](https://github.com/otto-nation/otto-workbench/commit/fa27e35c8fff5fd505d78e1c432cc092c9b1a28b))
* **gh,rebase:** honor inherited budget latch across processes; refuse unchecked rebases ([#1399](https://github.com/otto-nation/otto-workbench/issues/1399)) ([e99a13d](https://github.com/otto-nation/otto-workbench/commit/e99a13d8b039a74de21096d5aad89379f6f28bd0))
* **gh,retro:** refuse a rebase on a tracker read the breaker declined ([#1401](https://github.com/otto-nation/otto-workbench/issues/1401)) ([3d7e52d](https://github.com/otto-nation/otto-workbench/commit/3d7e52d0efd8357127bc0faa43eef268a1a33edb))
* **gh:** make the run and PR reads stop overclaiming their data ([#1379](https://github.com/otto-nation/otto-workbench/issues/1379)) ([0cc75bc](https://github.com/otto-nation/otto-workbench/commit/0cc75bccc81b70570cd6579d661deed6457e6168))
* **gh:** omit a null GraphQL variable instead of sending "None" ([#1503](https://github.com/otto-nation/otto-workbench/issues/1503)) ([72465cf](https://github.com/otto-nation/otto-workbench/commit/72465cf2e825262c53897acdbc1ad6e91f60b1d4))
* **gh:** report an incomplete thread fetch; cut GraphQL query cost ([#1371](https://github.com/otto-nation/otto-workbench/issues/1371)) ([5b64105](https://github.com/otto-nation/otto-workbench/commit/5b64105f231b5d103a249db399782bcef2a786b9))
* **gh:** stop rediscovering a spent API budget one call at a time ([#1359](https://github.com/otto-nation/otto-workbench/issues/1359)) ([b4efd35](https://github.com/otto-nation/otto-workbench/commit/b4efd353a53341ee08a1591a333036492a1c22d6))
* **gh:** tell a failed lookup from an authoritative empty answer ([#1376](https://github.com/otto-nation/otto-workbench/issues/1376)) ([3883e49](https://github.com/otto-nation/otto-workbench/commit/3883e49867bbd3461da49758fe062b4a9ec3dfa8))
* **git,sync:** repair credential helper before ai sync; never prompt ([#1667](https://github.com/otto-nation/otto-workbench/issues/1667)) ([270aba1](https://github.com/otto-nation/otto-workbench/commit/270aba15e7ff7a9a650bfad5016304d4746a4cd0))
* **git:** a failed git status no longer reads as a clean tree ([#974](https://github.com/otto-nation/otto-workbench/issues/974)) ([d895ba1](https://github.com/otto-nation/otto-workbench/commit/d895ba15786e3869a6e67d899d73d7cd56cc22a8))
* **git:** branch from a current default in bare worktrunk layouts ([#960](https://github.com/otto-nation/otto-workbench/issues/960)) ([af4f600](https://github.com/otto-nation/otto-workbench/commit/af4f600c176d41e2b05d038b584faceeb569ed70))
* **git:** correct worktrunk pre-switch hook append on BSD sed and document ANTHROPIC_API_KEY for --bare mode ([#1084](https://github.com/otto-nation/otto-workbench/issues/1084)) ([8224fe1](https://github.com/otto-nation/otto-workbench/commit/8224fe15ccc89b1ec04647651855a99bc564373f))
* **git:** fetch a branch before wt switch creates its worktree ([#1630](https://github.com/otto-nation/otto-workbench/issues/1630)) ([46f05e0](https://github.com/otto-nation/otto-workbench/commit/46f05e0658e138000c2a1ac3fcb886cfb9316453))
* **git:** install the hooks' tools on machines without Homebrew ([#1682](https://github.com/otto-nation/otto-workbench/issues/1682)) ([6383011](https://github.com/otto-nation/otto-workbench/commit/6383011b848d2f8844aeacc2a7f672ee0ccc630c))
* **git:** keep GitHub's SSH connection alive across a long pre-push ([#959](https://github.com/otto-nation/otto-workbench/issues/959)) ([77dba51](https://github.com/otto-nation/otto-workbench/commit/77dba517e4b3f32a55a8239bbdc498c77411a08d))
* **git:** keep the operator's work out of the regeneration commit ([#1311](https://github.com/otto-nation/otto-workbench/issues/1311)) ([07c09a1](https://github.com/otto-nation/otto-workbench/commit/07c09a1d31e38f770cafe5dae0327b47c46485fb))
* **git:** measure pre-push nesting against the pushed diff ([#1015](https://github.com/otto-nation/otto-workbench/issues/1015)) ([54d5cfd](https://github.com/otto-nation/otto-workbench/commit/54d5cfd655db74eb6a2e01c06fa7f99b898e94c7))
* **git:** refuse a commit whose signing would hang on pinentry ([#1681](https://github.com/otto-nation/otto-workbench/issues/1681)) ([c03d1e4](https://github.com/otto-nation/otto-workbench/commit/c03d1e4a4406a13fd877882dbcd695d9d97a5a21))
* **git:** verify a dropped push instead of reporting a failed gate ([#1303](https://github.com/otto-nation/otto-workbench/issues/1303)) ([f844b2c](https://github.com/otto-nation/otto-workbench/commit/f844b2c506af663357419d6ddc268877aa0a2a61))
* **guard:** tokenize commands, and cut the guard to what it can enforce ([#1483](https://github.com/otto-nation/otto-workbench/issues/1483)) ([df77323](https://github.com/otto-nation/otto-workbench/commit/df773239bfce44c2544c1b9d1fe7e5f657208f23))
* **hooks:** distinguish a gitleaks failure from a secret finding ([#1556](https://github.com/otto-nation/otto-workbench/issues/1556)) ([6b4a76f](https://github.com/otto-nation/otto-workbench/commit/6b4a76f46d7e08d4851f0f3f7efc8e95e7fd47c8))
* **lock:** let one process hold locks on several targets ([#1528](https://github.com/otto-nation/otto-workbench/issues/1528)) ([8bfbd6d](https://github.com/otto-nation/otto-workbench/commit/8bfbd6dea4121667b1fc3f51717cee66c082c99c))
* **mcp:** isolate tool subprocesses; fix false schema and collisions ([#1135](https://github.com/otto-nation/otto-workbench/issues/1135)) ([c89e82d](https://github.com/otto-nation/otto-workbench/commit/c89e82d33b8b72f9709a0e9d57bc41e47b5dad08))
* **mcp:** probe tools concurrently so a slow probe keeps its tool ([#975](https://github.com/otto-nation/otto-workbench/issues/975)) ([06e7119](https://github.com/otto-nation/otto-workbench/commit/06e7119f963ac605f358e45094b7e9a8e393ab08))
* **migrations:** defer a migration whose target does not exist yet ([#964](https://github.com/otto-nation/otto-workbench/issues/964)) ([1b698dc](https://github.com/otto-nation/otto-workbench/commit/1b698dc069cdb8839e1789ee8c76e08d2405d968))
* **migrations:** drop two redundant symlink-removal migrations ([#1561](https://github.com/otto-nation/otto-workbench/issues/1561)) ([43b8814](https://github.com/otto-nation/otto-workbench/commit/43b88143082a7702ad948956d4d4fb54dda8a7a3))
* **migrations:** park memory no repo keys instead of retrying forever ([#1647](https://github.com/otto-nation/otto-workbench/issues/1647)) ([b565ce6](https://github.com/otto-nation/otto-workbench/commit/b565ce6e1810c0b76bb7528d6e0a917f269baefd))
* **migrations:** treat an empty memory directory as nothing to carry ([#1554](https://github.com/otto-nation/otto-workbench/issues/1554)) ([c0800d2](https://github.com/otto-nation/otto-workbench/commit/c0800d264e67946fc81dd0547b72e5489d2cac67))
* **pi:** accept providers delegating to pi-ai's transport ([#1669](https://github.com/otto-nation/otto-workbench/issues/1669)) ([aa26c9a](https://github.com/otto-nation/otto-workbench/commit/aa26c9a7b9b6ae8761341a0500719cf9ac178f1a))
* **pi:** check model ids after package refresh, from the user's pi ([#1678](https://github.com/otto-nation/otto-workbench/issues/1678)) ([e09b2d4](https://github.com/otto-nation/otto-workbench/commit/e09b2d4eb692c71af5fa5c0aa85e01c9981050e4))
* **pi:** check the user's own pi in sync, and name a pin as the cure ([#1675](https://github.com/otto-nation/otto-workbench/issues/1675)) ([dde40d7](https://github.com/otto-nation/otto-workbench/commit/dde40d7b09071623373ee7a158c2c7b3775cfb52))
* **pi:** deliver superpowers bootstrap via system prompt ([#1571](https://github.com/otto-nation/otto-workbench/issues/1571)) ([f9ced37](https://github.com/otto-nation/otto-workbench/commit/f9ced37a505f525524f91d606344795534858f33))
* **pi:** gate packages on repo reachability, not org membership ([#1247](https://github.com/otto-nation/otto-workbench/issues/1247)) ([1d63372](https://github.com/otto-nation/otto-workbench/commit/1d6337277f55ea4928dc2384068d84eda80a1c97))
* **pi:** gate the install on pi being on PATH, not a fixed path ([#1118](https://github.com/otto-nation/otto-workbench/issues/1118)) ([9428eae](https://github.com/otto-nation/otto-workbench/commit/9428eae4acf83bb33dff404fdc0d64dce528b13f))
* **pi:** refuse unscoped test runners in review sessions ([#1497](https://github.com/otto-nation/otto-workbench/issues/1497)) ([1b5eb87](https://github.com/otto-nation/otto-workbench/commit/1b5eb87c4f63dd3f3dc0a61c972f825966d2e0e9))
* **pi:** skip _-prefixed extension dirs without warning ([#1406](https://github.com/otto-nation/otto-workbench/issues/1406)) ([52d4fc9](https://github.com/otto-nation/otto-workbench/commit/52d4fc9910ee5f81b2da88650ef18036d980eb44))
* **pr-ci:** read a cancelled run's failed jobs instead of dropping it ([#1336](https://github.com/otto-nation/otto-workbench/issues/1336)) ([9c0036b](https://github.com/otto-nation/otto-workbench/commit/9c0036bcfe358a253858b704e71d58255c753170))
* **pr-comments:** a draft round owes every table it printed ([#1054](https://github.com/otto-nation/otto-workbench/issues/1054)) ([e1e198d](https://github.com/otto-nation/otto-workbench/commit/e1e198d11a9593b65aa3e0d0467ac54d7577a252))
* **pr-comments:** a resolved thread is not evidence of a fix ([#1081](https://github.com/otto-nation/otto-workbench/issues/1081)) ([ff3af6d](https://github.com/otto-nation/otto-workbench/commit/ff3af6d3835bb7a694c317a56925d05ff8ddae92))
* **pr-comments:** a row the fix pass did not land cites no commit ([#1056](https://github.com/otto-nation/otto-workbench/issues/1056)) ([462a734](https://github.com/otto-nation/otto-workbench/commit/462a734653928d35ffa7859cc7f004c82c56b0ba))
* **pr-comments:** hold publishing on what the verify gate decided ([#1421](https://github.com/otto-nation/otto-workbench/issues/1421)) ([17eb13d](https://github.com/otto-nation/otto-workbench/commit/17eb13da45d0839ffdd1a8f4adb25749e7ac26af))
* **pr-comments:** scope a summary against its edited body, not its post time ([#1080](https://github.com/otto-nation/otto-workbench/issues/1080)) ([70ec1f0](https://github.com/otto-nation/otto-workbench/commit/70ec1f0d4517e91252bbb842f0f73e45353b1fc6))
* **pr:** check a hand-written PR body against the repo's template ([#1301](https://github.com/otto-nation/otto-workbench/issues/1301)) ([3829fb5](https://github.com/otto-nation/otto-workbench/commit/3829fb58e65c6205b3f4a05573a28b0eb9a24194))
* **pr:** create a worktree for a branch that has none from main ([#1685](https://github.com/otto-nation/otto-workbench/issues/1685)) ([39ddce0](https://github.com/otto-nation/otto-workbench/commit/39ddce0b0be35aa6af6af31bf46f1e8909bc7f1b))
* **pr:** follow a rebase when the closeout checks its fix commit ([#966](https://github.com/otto-nation/otto-workbench/issues/966)) ([11c8cf9](https://github.com/otto-nation/otto-workbench/commit/11c8cf962c250be6d80e8b755e67fa96323d41e5))
* **pr:** follow rebased fix commits via git's rewrite record ([#1584](https://github.com/otto-nation/otto-workbench/issues/1584)) ([1b81d7c](https://github.com/otto-nation/otto-workbench/commit/1b81d7c65b614c7113da1f2edbee14362a6067de))
* **pr:** give triage real code context and scope the fix agent's write grant ([#1551](https://github.com/otto-nation/otto-workbench/issues/1551)) ([562eba5](https://github.com/otto-nation/otto-workbench/commit/562eba5324655e998c1548c79212693c8b9dbff8))
* **pr:** keep a debt a later round did not raise ([#1391](https://github.com/otto-nation/otto-workbench/issues/1391)) ([babb456](https://github.com/otto-nation/otto-workbench/commit/babb4563a7131c7427d1e18d3747afed1e33bf02))
* **pr:** keep a decomposed comment item's identity, date and verdict ([#1338](https://github.com/otto-nation/otto-workbench/issues/1338)) ([7ad3e5e](https://github.com/otto-nation/otto-workbench/commit/7ad3e5eb2d3983dc9767692188077f73b0e2196a))
* **pr:** lease force-pushes against the replayed tip and lock the worktree written to ([#1390](https://github.com/otto-nation/otto-workbench/issues/1390)) ([6cf312c](https://github.com/otto-nation/otto-workbench/commit/6cf312c4ad385d13bb5d4d5d8b3d10f5f7149c31))
* **pr:** link the issue whether or not the repo has a PR template ([#1268](https://github.com/otto-nation/otto-workbench/issues/1268)) ([578e1bf](https://github.com/otto-nation/otto-workbench/commit/578e1bf96e4a9264e47d6192465687073986e62e))
* **pr:** pr create's follow-ups after it landed in [#1609](https://github.com/otto-nation/otto-workbench/issues/1609) ([#1624](https://github.com/otto-nation/otto-workbench/issues/1624)) ([de899f3](https://github.com/otto-nation/otto-workbench/commit/de899f343adae29a7554063f22c74916e9e151b1)), closes [#1585](https://github.com/otto-nation/otto-workbench/issues/1585)
* **pr:** record resolved threads in the persisted comment tally ([#992](https://github.com/otto-nation/otto-workbench/issues/992)) ([1a88985](https://github.com/otto-nation/otto-workbench/commit/1a88985a51030e81653089d688111eaead5cd138))
* **pr:** render every forge-specific string on the forge the repo is on ([#1536](https://github.com/otto-nation/otto-workbench/issues/1536)) ([86bdd43](https://github.com/otto-nation/otto-workbench/commit/86bdd430e711ec551accf4a4950a220a33f34031))
* **pr:** resolve a self-review locally; stop retrying permission denials ([#1346](https://github.com/otto-nation/otto-workbench/issues/1346)) ([66401ed](https://github.com/otto-nation/otto-workbench/commit/66401ed848443dc79048c033fb9f6de86bda9626))
* **pr:** resolve PR template and repo root independently of cwd and git env ([#1382](https://github.com/otto-nation/otto-workbench/issues/1382)) ([1ab1e5c](https://github.com/otto-nation/otto-workbench/commit/1ab1e5c3e005771ae0670400313b93b12d67df2c))
* **pr:** resume a paused rebase without naming the branch ([#1625](https://github.com/otto-nation/otto-workbench/issues/1625)) ([f796d4a](https://github.com/otto-nation/otto-workbench/commit/f796d4a27fd3629746f49f6f2f2cf3cb80a27553))
* **pr:** send --pr runs to the head branch's worktree ([#1617](https://github.com/otto-nation/otto-workbench/issues/1617)) ([3cf6975](https://github.com/otto-nation/otto-workbench/commit/3cf69750c67777f30d4c27b6693bf4f52baea43a))
* **pr:** settle a thread the PR already shows as answered ([#1334](https://github.com/otto-nation/otto-workbench/issues/1334)) ([36e9ca3](https://github.com/otto-nation/otto-workbench/commit/36e9ca3726c266958568c1e20ab7fa1cb4419271))
* **pr:** single-brace the triage prompt's comment_items schema ([#1245](https://github.com/otto-nation/otto-workbench/issues/1245)) ([7e1b662](https://github.com/otto-nation/otto-workbench/commit/7e1b662a6c42afaad364e30a26a7c20d8b3f5de8))
* **pr:** stop citing a commit the row's evidence no longer supports ([#1531](https://github.com/otto-nation/otto-workbench/issues/1531)) ([b8af0ae](https://github.com/otto-nation/otto-workbench/commit/b8af0aeea4b5e2bd05ac5b66f9132012a84526af))
* **pr:** stop trusting cached PR state past the commit it describes ([#1527](https://github.com/otto-nation/otto-workbench/issues/1527)) ([288ac7b](https://github.com/otto-nation/otto-workbench/commit/288ac7b8767741dc3fd4f3db47c10c4a749616c9))
* **push-intent:** a landed branch is not a push that vanished ([#1020](https://github.com/otto-nation/otto-workbench/issues/1020)) ([c0d54f7](https://github.com/otto-nation/otto-workbench/commit/c0d54f7d9ef40468666eba536a2f09b2365f2082))
* **push:** give the bash bridge the trail its siblings already have ([#1553](https://github.com/otto-nation/otto-workbench/issues/1553)) ([0fde35c](https://github.com/otto-nation/otto-workbench/commit/0fde35cb27f66762d382d2c1464b43d66f1c326c))
* **push:** tell an auth refusal apart from a network one, and say which ([#1486](https://github.com/otto-nation/otto-workbench/issues/1486)) ([4b2e6ea](https://github.com/otto-nation/otto-workbench/commit/4b2e6eae780596b5437568f2c555666ef5c1a70f))
* **rebase:** clamp conflict context; stop aborting on one bad resolution ([#1567](https://github.com/otto-nation/otto-workbench/issues/1567)) ([447c904](https://github.com/otto-nation/otto-workbench/commit/447c9043a3e8d1dedfd35a3e56d641cb386d8bee))
* **rebase:** lease a force-push against the tip it replayed ([#1383](https://github.com/otto-nation/otto-workbench/issues/1383)) ([2a32aea](https://github.com/otto-nation/otto-workbench/commit/2a32aea7e858eb10484eb2d28e6fcc801c657df3))
* **rebase:** reject resolutions that echo their context; hold rerere off ([#1484](https://github.com/otto-nation/otto-workbench/issues/1484)) ([43553c4](https://github.com/otto-nation/otto-workbench/commit/43553c45b4874af67b486a7fd14b8c190c91753a))
* **rebase:** stop the skill detaching its own job; guard it under Pi ([#1307](https://github.com/otto-nation/otto-workbench/issues/1307)) ([f57d7aa](https://github.com/otto-nation/otto-workbench/commit/f57d7aa1940cfb00ae5bbd1322ea2f879e853bb2))
* regen staging; drop fsmonitor; clarify doc rule ([#1149](https://github.com/otto-nation/otto-workbench/issues/1149), [#1140](https://github.com/otto-nation/otto-workbench/issues/1140), [#1125](https://github.com/otto-nation/otto-workbench/issues/1125)) ([#1152](https://github.com/otto-nation/otto-workbench/issues/1152)) ([f7a80e2](https://github.com/otto-nation/otto-workbench/commit/f7a80e24a6d4d380f1ce2d90dec96a815d4d7beb))
* repair two state bugs — a pinned root and a dropped selection ([#1100](https://github.com/otto-nation/otto-workbench/issues/1100)) ([7bac4ea](https://github.com/otto-nation/otto-workbench/commit/7bac4ea6b1dd45ed8627552dad241e200acde217))
* **retro,tests:** keep threads a failed refetch would drop; sandbox the cache root ([#1407](https://github.com/otto-nation/otto-workbench/issues/1407)) ([661a1b4](https://github.com/otto-nation/otto-workbench/commit/661a1b4567086d6b4a257e9e531d12aec8203401))
* **retro:** bound the review deletion to the scan that earned it ([#1356](https://github.com/otto-nation/otto-workbench/issues/1356)) ([7a5d99e](https://github.com/otto-nation/otto-workbench/commit/7a5d99e75299205538b13d3e236c1de04be90b60))
* **retro:** expand registry paths so the scan resolves its GitHub repos ([#1344](https://github.com/otto-nation/otto-workbench/issues/1344)) ([52154e8](https://github.com/otto-nation/otto-workbench/commit/52154e8b686ce4620c09c176edfb596ccf1c3103))
* **retro:** report a passage snippet for a rule file with no bullets ([#1337](https://github.com/otto-nation/otto-workbench/issues/1337)) ([bb9b2ff](https://github.com/otto-nation/otto-workbench/commit/bb9b2ff3fbab95c701c1c0b89e3f35ccc5ac9684))
* **retro:** score rules by passage so gaps are reported ([#1299](https://github.com/otto-nation/otto-workbench/issues/1299)) ([95bbde6](https://github.com/otto-nation/otto-workbench/commit/95bbde6bb4cf3e268be2d28a0abd1a13457b4a3a))
* **review-guard:** detect writes by flag position and parse every -c spelling ([#1480](https://github.com/otto-nation/otto-workbench/issues/1480)) ([e549560](https://github.com/otto-nation/otto-workbench/commit/e549560b999cdb4e1f599b6b7fd9420aa19e63a0))
* **review-threads:** attribute reconciled work per row, not per branch ([#1110](https://github.com/otto-nation/otto-workbench/issues/1110)) ([893f236](https://github.com/otto-nation/otto-workbench/commit/893f2365b16f668c1d14b8974daf01112d85b14c)), closes [#1096](https://github.com/otto-nation/otto-workbench/issues/1096)
* **review:** a non-UTF-8 file in the diff aborts the whole review run ([#1341](https://github.com/otto-nation/otto-workbench/issues/1341)) ([474914d](https://github.com/otto-nation/otto-workbench/commit/474914d345abdc4bf035f17cf82c8ad79b0487de))
* **review:** a prior finding nobody accounted for reaches synthesis ([#1053](https://github.com/otto-nation/otto-workbench/issues/1053)) ([530d988](https://github.com/otto-nation/otto-workbench/commit/530d988b1157bfb15658965e0f24e4470daafb80))
* **review:** accept markdown emphasis after a prior-finding verdict ([#1297](https://github.com/otto-nation/otto-workbench/issues/1297)) ([7bf9386](https://github.com/otto-nation/otto-workbench/commit/7bf9386e7aee036ab241b301839819747ddfc2d0))
* **review:** budget the 5.5 Sonnet and Opus models ([#1572](https://github.com/otto-nation/otto-workbench/issues/1572)) ([9128b10](https://github.com/otto-nation/otto-workbench/commit/9128b109a4c17f2db7ace800b35a532ceadaf844))
* **review:** charge the diff block what it rendered, not its allowance ([#1264](https://github.com/otto-nation/otto-workbench/issues/1264)) ([4908fb5](https://github.com/otto-nation/otto-workbench/commit/4908fb506a8c9cbddbf51e223adad6a1ae7101d6))
* **review:** check declines and keep wrapped reasons whole ([#1418](https://github.com/otto-nation/otto-workbench/issues/1418)) ([d7bdd30](https://github.com/otto-nation/otto-workbench/commit/d7bdd3032fb987dd9c9f5c1689b1f90388ebb407))
* **review:** count review profiles, and measure prompt tokens by default ([#1253](https://github.com/otto-nation/otto-workbench/issues/1253)) ([fc7ac9c](https://github.com/otto-nation/otto-workbench/commit/fc7ac9ccf891629487e5a83b521d59953cc6f5da))
* **review:** derive a stacked branch's real base instead of the trunk ([#1435](https://github.com/otto-nation/otto-workbench/issues/1435)) ([fa45d48](https://github.com/otto-nation/otto-workbench/commit/fa45d48814fa75338f8c939b2702942bd4bb5ee4))
* **review:** derive the prompt ceiling from the model's context window ([#1330](https://github.com/otto-nation/otto-workbench/issues/1330)) ([a367193](https://github.com/otto-nation/otto-workbench/commit/a367193467603f889e881077dd2805d51b72dc80))
* **review:** end every review range at the stamped head, not live HEAD ([#1578](https://github.com/otto-nation/otto-workbench/issues/1578)) ([61168a2](https://github.com/otto-nation/otto-workbench/commit/61168a23bab67081617b81d7ce72c61bed650b0e))
* **review:** escape prose cells in the deferred tracking issue ([#1310](https://github.com/otto-nation/otto-workbench/issues/1310)) ([055b611](https://github.com/otto-nation/otto-workbench/commit/055b6116c4d989ebb9769fc1f44dd0a7d823d5b0))
* **review:** file the tracking issue on config; report a bad --track ([#1335](https://github.com/otto-nation/otto-workbench/issues/1335)) ([f7b8651](https://github.com/otto-nation/otto-workbench/commit/f7b86513b34d70948d4c4070ae9d963cf1a26cb8))
* **review:** fit every prompt to its budget, or fail the phase ([#1041](https://github.com/otto-nation/otto-workbench/issues/1041)) ([a007f57](https://github.com/otto-nation/otto-workbench/commit/a007f57115f5e0330c67da2ce3adca39a11e9878))
* **review:** forbid unrun execution claims; give the guard one owner ([#1562](https://github.com/otto-nation/otto-workbench/issues/1562)) ([d3fa91d](https://github.com/otto-nation/otto-workbench/commit/d3fa91d64e68c77191cb3a463beb7195ec2fb0d5))
* **review:** harness writes the review artifact's head_sha marker ([#1266](https://github.com/otto-nation/otto-workbench/issues/1266)) ([42e5c0d](https://github.com/otto-nation/otto-workbench/commit/42e5c0d9fb954153a1acc5b2d44c969be863e45d))
* **review:** hedge a fix the verify gate could not stand behind ([#1450](https://github.com/otto-nation/otto-workbench/issues/1450)) ([95a73b8](https://github.com/otto-nation/otto-workbench/commit/95a73b84eff42391de9e7623f6f164e2f488d7cd))
* **review:** keep a prior-findings tally out of the review summary ([#1534](https://github.com/otto-nation/otto-workbench/issues/1534)) ([c16a918](https://github.com/otto-nation/otto-workbench/commit/c16a91896f328696b9afb236df3a0170b3740196))
* **review:** keep the prior-findings ledger out of the posted body ([#1532](https://github.com/otto-nation/otto-workbench/issues/1532)) ([cbe403f](https://github.com/otto-nation/otto-workbench/commit/cbe403f784a53206f4d365d3a13ac26b9574af48))
* **review:** keep what a review found when a run ends early ([#1522](https://github.com/otto-nation/otto-workbench/issues/1522)) ([1475bde](https://github.com/otto-nation/otto-workbench/commit/1475bde12460adae2cccea91e052a9a90b9b4d8f))
* **review:** lock the trees a review switches to and writes ([#1389](https://github.com/otto-nation/otto-workbench/issues/1389)) ([38cf455](https://github.com/otto-nation/otto-workbench/commit/38cf45543b4124f3150969512e2d20224a0e89e7))
* **review:** post via pr review --post; validate prose CLI refs ([#1670](https://github.com/otto-nation/otto-workbench/issues/1670)) ([bb16d74](https://github.com/otto-nation/otto-workbench/commit/bb16d742debdb6aaee04110cc73d28adf84c8455))
* **review:** posting answers a fixed and a declined finding apart ([#1065](https://github.com/otto-nation/otto-workbench/issues/1065)) ([0493f91](https://github.com/otto-nation/otto-workbench/commit/0493f9151d2e130f043f227ce3b5b19384a7f286))
* **review:** reconcile prior findings that state a verdict or name a file ([#982](https://github.com/otto-nation/otto-workbench/issues/982)) ([c9afbce](https://github.com/otto-nation/otto-workbench/commit/c9afbce315337c9941da7f36c65b2f3b894cc2e0))
* **review:** refuse --reply beside a phase flag; test and share thread reads ([#1380](https://github.com/otto-nation/otto-workbench/issues/1380)) ([c5ff1a5](https://github.com/otto-nation/otto-workbench/commit/c5ff1a5062faacce0b9f08ca5d0f395d716c20d9))
* **review:** refuse a mistyped --base instead of reviewing nothing ([#1438](https://github.com/otto-nation/otto-workbench/issues/1438)) ([0a2ab10](https://github.com/otto-nation/otto-workbench/commit/0a2ab1033fe648f6ccc4f33ebc48a6aeff66bdf1))
* **review:** refuse to publish a review another run wrote ([#1393](https://github.com/otto-nation/otto-workbench/issues/1393)) ([eee1ca3](https://github.com/otto-nation/otto-workbench/commit/eee1ca3e90faf261ea2e9cac78706de78f158a01)), closes [#1386](https://github.com/otto-nation/otto-workbench/issues/1386)
* **review:** require a size floor before grouping earns an agent ([#1491](https://github.com/otto-nation/otto-workbench/issues/1491)) ([87952e4](https://github.com/otto-nation/otto-workbench/commit/87952e4e6e6e0d40a5dbd6ffd3c02b6ea95c4a48))
* **review:** route GitHub issue calls to the host base_url names ([#1434](https://github.com/otto-nation/otto-workbench/issues/1434)) ([35ec3d7](https://github.com/otto-nation/otto-workbench/commit/35ec3d7e7db50eb70045501d6e2cadf68e45b03d))
* **review:** scope the incremental delta by ancestry, not by path ([#1269](https://github.com/otto-nation/otto-workbench/issues/1269)) ([1c133b4](https://github.com/otto-nation/otto-workbench/commit/1c133b4de879282ac0aac111721b855d87dcaa97)), closes [#1256](https://github.com/otto-nation/otto-workbench/issues/1256)
* **review:** shed sparse files only when the collection overflows ([#1452](https://github.com/otto-nation/otto-workbench/issues/1452)) ([5c8e275](https://github.com/otto-nation/otto-workbench/commit/5c8e27525495fed4afabf4d5a29694feebaf52c3))
* **review:** stop gc deleting a review dir a run just created ([#1408](https://github.com/otto-nation/otto-workbench/issues/1408)) ([340493e](https://github.com/otto-nation/otto-workbench/commit/340493ed6fef522d820e5fe45ec026d50d742e9e))
* **review:** stop offering recovery for an oversized prompt ([#1214](https://github.com/otto-nation/otto-workbench/issues/1214)) ([8634fc4](https://github.com/otto-nation/otto-workbench/commit/8634fc4d1507a1fb255f2ac847028a23c23afe99))
* **review:** stop reposting deduplicated findings in the review body ([#1672](https://github.com/otto-nation/otto-workbench/issues/1672)) ([d1a4d6c](https://github.com/otto-nation/otto-workbench/commit/d1a4d6c77bf499f22221ed41e98012f249908059))
* **review:** take the origin's host only when it names the repo under review ([#1454](https://github.com/otto-nation/otto-workbench/issues/1454)) ([c3cff27](https://github.com/otto-nation/otto-workbench/commit/c3cff275f9c0e879b64be64de15a0891b3e369bc))
* **review:** verify prompts against the model's token budget ([#1605](https://github.com/otto-nation/otto-workbench/issues/1605)) ([5d78f51](https://github.com/otto-nation/otto-workbench/commit/5d78f51c34900c6c16b2cf901003f0f67345c2a9))
* **review:** verify the poster's path; stop children on kill ([#1599](https://github.com/otto-nation/otto-workbench/issues/1599)) ([aa5f14e](https://github.com/otto-nation/otto-workbench/commit/aa5f14ee944378c2ac6016e5f6ba75aef4228023))
* **review:** write prompt-stats.json atomically under a lock ([#1490](https://github.com/otto-nation/otto-workbench/issues/1490)) ([a7fa658](https://github.com/otto-nation/otto-workbench/commit/a7fa658492efeb974a45c3c5b99aa7be3f83ee4f))
* **rules:** let the shell and secrets rules reach Pi ([#1117](https://github.com/otto-nation/otto-workbench/issues/1117)) ([77705fe](https://github.com/otto-nation/otto-workbench/commit/77705fe3a4c2d810e925132d1e7394b9cceadf66))
* **rules:** stop harness-scoped prose hiding universal rules ([#1312](https://github.com/otto-nation/otto-workbench/issues/1312)) ([e0762bc](https://github.com/otto-nation/otto-workbench/commit/e0762bc88e64977df63f75b6e6d967a21ed520c4))
* self-review findings ([#1573](https://github.com/otto-nation/otto-workbench/issues/1573)) ([74bbd58](https://github.com/otto-nation/otto-workbench/commit/74bbd587c497fbee318727a97dc024bf36a385ff))
* **sessions:** address Pi's store with Pi's own transform ([#1374](https://github.com/otto-nation/otto-workbench/issues/1374)) ([60e48f3](https://github.com/otto-nation/otto-workbench/commit/60e48f31f387aaf2c8ae04102dc6173c4562d1f7))
* **sessions:** pin _encode_slug to a UTF-8 locale macOS has ([#1646](https://github.com/otto-nation/otto-workbench/issues/1646)) ([a4043aa](https://github.com/otto-nation/otto-workbench/commit/a4043aad72a379aef9e45f66ffc3e04de63b3c18))
* **setup:** bootstrap mise and install worktrunk in core on Linux ([#1684](https://github.com/otto-nation/otto-workbench/issues/1684)) ([1ddeaf5](https://github.com/otto-nation/otto-workbench/commit/1ddeaf52dd9bc15519b06f9806dc16eefd2d1ab1))
* **skills:** make the session gates and their consumers harness-neutral ([#1353](https://github.com/otto-nation/otto-workbench/issues/1353)) ([1294137](https://github.com/otto-nation/otto-workbench/commit/129413749e81643eb2baec9f772330f1c6e93e85))
* **skills:** reject an empty --tool-schema document under jq 1.6 ([#1636](https://github.com/otto-nation/otto-workbench/issues/1636)) ([aab1d07](https://github.com/otto-nation/otto-workbench/commit/aab1d070051ec01c15b26ecf7d29b910475f3f17))
* **skills:** worktree shim reports the default branch's worktree as isolated ([#1277](https://github.com/otto-nation/otto-workbench/issues/1277)) ([9087c64](https://github.com/otto-nation/otto-workbench/commit/9087c64c2bea782d1c4e61b32e722eeae05a076f))
* **sync:** put user tool dirs on PATH so snippets are not removed ([#1668](https://github.com/otto-nation/otto-workbench/issues/1668)) ([585fd88](https://github.com/otto-nation/otto-workbench/commit/585fd880f445933c0f52110d628aab6d254e7d41))
* **sync:** rename legacy model vars and skip herdr update in a pane ([#1677](https://github.com/otto-nation/otto-workbench/issues/1677)) ([907c8fa](https://github.com/otto-nation/otto-workbench/commit/907c8faed01d8810ad1a0d36978d0ed6760abee4))
* **sync:** warn on placeholder git identity and an unread Pi model ([#1676](https://github.com/otto-nation/otto-workbench/issues/1676)) ([85f4e68](https://github.com/otto-nation/otto-workbench/commit/85f4e6847bb2bf4c5a18727c6e6a171249e40c6c))
* **task:** add WORKBENCH_LIB_DIR to source lib/ai from a branch ([#1302](https://github.com/otto-nation/otto-workbench/issues/1302)) ([10c6e7e](https://github.com/otto-nation/otto-workbench/commit/10c6e7e6741fde9448fdd59733a0b72a49b165b6))
* **test:** always run suites that scan the real registry tree ([#1663](https://github.com/otto-nation/otto-workbench/issues/1663)) ([06a9334](https://github.com/otto-nation/otto-workbench/commit/06a933431480b7e1ec608de38c5b23e1714f74a0))
* **test:** give the process-tree deadline case room to fork ([#1620](https://github.com/otto-nation/otto-workbench/issues/1620)) ([b6558ff](https://github.com/otto-nation/otto-workbench/commit/b6558ff2067993a0e99558aad188c01950876fa6))
* **test:** hide pi without narrowing PATH to a fixed list ([#1606](https://github.com/otto-nation/otto-workbench/issues/1606)) ([e3abac9](https://github.com/otto-nation/otto-workbench/commit/e3abac9f646dde7b33762863d2025da3f5e3c8b7))
* **test:** keep node and bash reachable in the resolver-path case ([#1632](https://github.com/otto-nation/otto-workbench/issues/1632)) ([739f589](https://github.com/otto-nation/otto-workbench/commit/739f589efbf98972f3a59e2d3bb7666a11b3c394))
* **test:** make a slow suite tell you what it is doing ([#1607](https://github.com/otto-nation/otto-workbench/issues/1607)) ([f2b9068](https://github.com/otto-nation/otto-workbench/commit/f2b90686808869c02954ffe54a9ce6b9e1db1589))
* **tests:** detach pytest temp repos from the machine's git config ([#1131](https://github.com/otto-nation/otto-workbench/issues/1131)) ([7824306](https://github.com/otto-nation/otto-workbench/commit/78243064f1e2b82da8ad7b2af21af5221b4c2e44))
* **tests:** isolate test repos from the developer's git config ([#986](https://github.com/otto-nation/otto-workbench/issues/986)) ([d3d1d49](https://github.com/otto-nation/otto-workbench/commit/d3d1d4933bc1ac87cac8ac7a1832805748197c7a))
* **tests:** link the real binary behind a version-manager shim in narrow_path_to ([#1674](https://github.com/otto-nation/otto-workbench/issues/1674)) ([60a49bf](https://github.com/otto-nation/otto-workbench/commit/60a49bf342ae1b297833c16a2464cc53d6ceacbf))
* **tests:** name the pytest shadowing the one that has xdist ([#1482](https://github.com/otto-nation/otto-workbench/issues/1482)) ([dce7d66](https://github.com/otto-nation/otto-workbench/commit/dce7d664b69dfa5b152c1337aea4a1e74862e6c7))
* **tests:** report the parallelism a suite run resolved ([#1423](https://github.com/otto-nation/otto-workbench/issues/1423)) ([9d524cc](https://github.com/otto-nation/otto-workbench/commit/9d524cca5c4875268ff9c5635670638d7e8c23d2))
* **tests:** sandbox the suite from the machine's git hooks ([#958](https://github.com/otto-nation/otto-workbench/issues/958)) ([59931d7](https://github.com/otto-nation/otto-workbench/commit/59931d734ce0d469beb44d99fbe14922965b9a9f))
* **tests:** stop a failed bats setup from wiping the real $TMPDIR ([#1351](https://github.com/otto-nation/otto-workbench/issues/1351)) ([95e696f](https://github.com/otto-nation/otto-workbench/commit/95e696f2fa72b559fdf0a3f988f563ec434bc431))
* **tests:** stop a stubbed registry scan costing pre-push minutes ([#1583](https://github.com/otto-nation/otto-workbench/issues/1583)) ([51e0c6c](https://github.com/otto-nation/otto-workbench/commit/51e0c6cbd165627fcc5ce6e567e97c56400ed238))
* **tests:** stop PATH shims exec-ing themselves under a version manager ([#1209](https://github.com/otto-nation/otto-workbench/issues/1209)) ([1879ad9](https://github.com/otto-nation/otto-workbench/commit/1879ad92126b6294bcd3c7dc421ba30539dd26e1))
* **tests:** undo the git config write the guard catches ([#941](https://github.com/otto-nation/otto-workbench/issues/941)) ([f8c0bd0](https://github.com/otto-nation/otto-workbench/commit/f8c0bd0420f3e192da8924d6db20a897d3d4a6cf))
* **trail:** record an evidence-less fixed tick where otto-log finds it ([#1384](https://github.com/otto-nation/otto-workbench/issues/1384)) ([4f23d7d](https://github.com/otto-nation/otto-workbench/commit/4f23d7dc18ff30757e6f7c4cd95a2814cb2ebf31))
* **tree-lock:** tell a broken probe apart from a free tree ([#1673](https://github.com/otto-nation/otto-workbench/issues/1673)) ([144d97b](https://github.com/otto-nation/otto-workbench/commit/144d97bf0dc5790e080175093dc584910f4c6e7e))
* verify claimed outcomes before reporting them ([#1658](https://github.com/otto-nation/otto-workbench/issues/1658)) ([ca58539](https://github.com/otto-nation/otto-workbench/commit/ca585394e1c9e3f3571d323a57185212e110f634))
* **vertex:** make Vertex the working default for every model family ([#1204](https://github.com/otto-nation/otto-workbench/issues/1204)) ([9b7873d](https://github.com/otto-nation/otto-workbench/commit/9b7873d049508b20ff3f0802706d9f6619239706))
* **wiki:** escape source_type in frontmatter, not at the call site ([#1180](https://github.com/otto-nation/otto-workbench/issues/1180)) ([8ab4a4f](https://github.com/otto-nation/otto-workbench/commit/8ab4a4f73eff5b5c7cd86309f5bbf894ef847aad))
* **wiki:** require articles/ and raw/, not just SCHEMA.md ([#1250](https://github.com/otto-nation/otto-workbench/issues/1250)) ([c43ca5a](https://github.com/otto-nation/otto-workbench/commit/c43ca5a2159e0eea53713c24a88d0beca6cdbcb9))
* **wt-cleanup:** adopt wt list schema 2 ([#1198](https://github.com/otto-nation/otto-workbench/issues/1198)) ([12c4567](https://github.com/otto-nation/otto-workbench/commit/12c45672fa5640a16e049a17adfd49209cdb09f0))
* **wt-cleanup:** delete the branch a merged worktree leaves behind ([#1031](https://github.com/otto-nation/otto-workbench/issues/1031)) ([f1c3834](https://github.com/otto-nation/otto-workbench/commit/f1c38345aa20ce1bfe51609b242b953c69529a02))
* **wt-cleanup:** judge a merged worktree's residue against main ([#1063](https://github.com/otto-nation/otto-workbench/issues/1063)) ([0563ded](https://github.com/otto-nation/otto-workbench/commit/0563dedafbfba4f10e12d67314cbd0a38232a826))
* **wt-cleanup:** remove worktrees in the foreground ([#1582](https://github.com/otto-nation/otto-workbench/issues/1582)) ([2070aff](https://github.com/otto-nation/otto-workbench/commit/2070aff6759bbe1147875f26ac6bcd0f8d5ef92c))
* **wt:** keep a branch that has no commits rather than reading it as merged ([#1570](https://github.com/otto-nation/otto-workbench/issues/1570)) ([8e3f574](https://github.com/otto-nation/otto-workbench/commit/8e3f574bd4dfab79a43f44c58026f8d0a248b598))
* **zsh,git:** activate mise on Linux; detect usable credential helper ([#1666](https://github.com/otto-nation/otto-workbench/issues/1666)) ([70c9976](https://github.com/otto-nation/otto-workbench/commit/70c9976bc5abd8eb17ad356736d41674620c8586))
* **zsh:** install the shell's prompt, framework and plugins without Homebrew ([#1683](https://github.com/otto-nation/otto-workbench/issues/1683)) ([d38a80e](https://github.com/otto-nation/otto-workbench/commit/d38a80ee6f4de460aadcfb2593ab37f2248e4f9a))
* **zsh:** skip brew plugin glob when Homebrew is absent ([#1665](https://github.com/otto-nation/otto-workbench/issues/1665)) ([435d5c8](https://github.com/otto-nation/otto-workbench/commit/435d5c80d3e400d3bede5fedd52633315a79b415))


### Performance Improvements

* **guard:** one statement scan, six fewer forks ([#1545](https://github.com/otto-nation/otto-workbench/issues/1545)) ([0376b04](https://github.com/otto-nation/otto-workbench/commit/0376b049d219af1c8d5f0965bd3ab450b520b8d5))
* **projects:** the sync drops registry entries whose work tree is gone ([#1057](https://github.com/otto-nation/otto-workbench/issues/1057)) ([da67131](https://github.com/otto-nation/otto-workbench/commit/da671313c8d691f334ade73b742a51b0a769bea0))
* **review:** size group parallelism from free capacity; raise self-review depth ([#1495](https://github.com/otto-nation/otto-workbench/issues/1495)) ([86b74a1](https://github.com/otto-nation/otto-workbench/commit/86b74a1644eebdaeba8e24d7b184dbf795deeb6e))
* **tests:** divide test parallelism across concurrent worktree runs ([#1478](https://github.com/otto-nation/otto-workbench/issues/1478)) ([96a33a7](https://github.com/otto-nation/otto-workbench/commit/96a33a748e56ee13249341f51e6731f31be953b2))
* **tests:** run both suites through one parallel runner ([#940](https://github.com/otto-nation/otto-workbench/issues/940)) ([9d9d40e](https://github.com/otto-nation/otto-workbench/commit/9d9d40e912d7e97ede6e5be6aa0a43d203ab5be8))
* **tests:** shard on measured runtime; cut the forks behind the slowest suite ([#1506](https://github.com/otto-nation/otto-workbench/issues/1506)) ([3b442f2](https://github.com/otto-nation/otto-workbench/commit/3b442f22acdff23848c4801b9be4edee0e33cc47))
* **validate:** run validators concurrently; batch registry reads ([#1216](https://github.com/otto-nation/otto-workbench/issues/1216)) ([d4d9652](https://github.com/otto-nation/otto-workbench/commit/d4d96527f0aedc36aa812a993925f5073181a40e))


### Dependencies

* bump @otto-nation/brand to 1.0.1 ([#989](https://github.com/otto-nation/otto-workbench/issues/989)) ([acd0049](https://github.com/otto-nation/otto-workbench/commit/acd0049aed60fec75559070eb97b47f1a9045fbc))
* bump @otto-nation/brand to 1.0.2 ([#1281](https://github.com/otto-nation/otto-workbench/issues/1281)) ([e74ce14](https://github.com/otto-nation/otto-workbench/commit/e74ce146fffe4a9c9a91ca38f06c8a36850bf153))
* bump @otto-nation/brand to 1.1.0 ([#1305](https://github.com/otto-nation/otto-workbench/issues/1305)) ([f54b41f](https://github.com/otto-nation/otto-workbench/commit/f54b41f828d1bda6b0c78133676b656b5a357dcc))
* bump @otto-nation/brand to 1.2.0 ([#1619](https://github.com/otto-nation/otto-workbench/issues/1619)) ([dcc72b0](https://github.com/otto-nation/otto-workbench/commit/dcc72b026dc05057b583011085cf63eb14892e81))


### Code Refactoring

* **ai:** call the rebase library in-process from ci-check ([#1182](https://github.com/otto-nation/otto-workbench/issues/1182)) ([406b0d5](https://github.com/otto-nation/otto-workbench/commit/406b0d5d5b9692463e8f5f896c63c6030fa21379))
* **ai:** decompose pr-rebase — types, inspect, conflicts, resolve_ai ([#1162](https://github.com/otto-nation/otto-workbench/issues/1162)) ([2446250](https://github.com/otto-nation/otto-workbench/commit/244625070434b0d7d23c43a6159342c852d98dbd))
* **ai:** dispatch pr subcommands in-process ([#1543](https://github.com/otto-nation/otto-workbench/issues/1543)) ([9fda861](https://github.com/otto-nation/otto-workbench/commit/9fda861c09e664c35b52eb6431f55b942b90537f))
* **ai:** dissolve the review's shared-helper module ([#1052](https://github.com/otto-nation/otto-workbench/issues/1052)) ([281dea8](https://github.com/otto-nation/otto-workbench/commit/281dea8f5d449b48226a61d9028b5f4de52cec85))
* **ai:** fold FixSummary's outcomes into FixRecord ([#1004](https://github.com/otto-nation/otto-workbench/issues/1004)) ([66dd0fb](https://github.com/otto-nation/otto-workbench/commit/66dd0fb587d540269678659dadb893d6415ba152))
* **ai:** fold the pr status dashboard into the library; sweep via migration ([#1340](https://github.com/otto-nation/otto-workbench/issues/1340)) ([ced4ebb](https://github.com/otto-nation/otto-workbench/commit/ced4ebbc0060325b8f1e027793b4bca6402bc0a8))
* **ai:** give ai/bin's small commands an importable home in cli/ ([#1146](https://github.com/otto-nation/otto-workbench/issues/1146)) ([c85c1a9](https://github.com/otto-nation/otto-workbench/commit/c85c1a94de9b9f6a2c9d8966ffe0719126034fe9))
* **ai:** give collection and its budget one owner ([#1082](https://github.com/otto-nation/otto-workbench/issues/1082)) ([8d06a4c](https://github.com/otto-nation/otto-workbench/commit/8d06a4c67383c8f042fd13a5a4a580a321ec7d6c))
* **ai:** give commit attribution an owner ([#1206](https://github.com/otto-nation/otto-workbench/issues/1206)) ([1216af1](https://github.com/otto-nation/otto-workbench/commit/1216af1de56702c4918eb4c8df17ba4bf49aa274))
* **ai:** give every domain a fix record ([#947](https://github.com/otto-nation/otto-workbench/issues/947)) ([e762508](https://github.com/otto-nation/otto-workbench/commit/e762508e68d38084b41700d19f950b37e23ba974))
* **ai:** give permalinks and markdown cells an owner ([#1202](https://github.com/otto-nation/otto-workbench/issues/1202)) ([c594feb](https://github.com/otto-nation/otto-workbench/commit/c594febf67a62c6ed7bf7a910c6b6fa5adbc5530))
* **ai:** give pr a command registry ([#1499](https://github.com/otto-nation/otto-workbench/issues/1499)) ([29cb0df](https://github.com/otto-nation/otto-workbench/commit/29cb0df9c1f2cf2217c621a47f67b6a5bb32a44a)), closes [#909](https://github.com/otto-nation/otto-workbench/issues/909)
* **ai:** give pr an importable handler seam ([#1508](https://github.com/otto-nation/otto-workbench/issues/1508)) ([1cfc61d](https://github.com/otto-nation/otto-workbench/commit/1cfc61d93afc46a4d2289760314d99f3de709ec6)), closes [#909](https://github.com/otto-nation/otto-workbench/issues/909)
* **ai:** give review grouping one owner ([#1077](https://github.com/otto-nation/otto-workbench/issues/1077)) ([b6d6a3f](https://github.com/otto-nation/otto-workbench/commit/b6d6a3f694edbdea0fc9593691c5833bf95bea7d))
* **ai:** give review_findings' residue owners; delete the module ([#1075](https://github.com/otto-nation/otto-workbench/issues/1075)) ([33f4823](https://github.com/otto-nation/otto-workbench/commit/33f48237fd9ad8b136c7355655b0a6200e1e6bb4))
* **ai:** give review_preflight's names owners; delete the module ([#1090](https://github.com/otto-nation/otto-workbench/issues/1090)) ([8063ffb](https://github.com/otto-nation/otto-workbench/commit/8063ffb9f0e80864c0be7434ed37936e39eaed54))
* **ai:** give the agent phase registry its own module and env keys ([#985](https://github.com/otto-nation/otto-workbench/issues/985)) ([e013a41](https://github.com/otto-nation/otto-workbench/commit/e013a4191e2d0d9928d371e2d39d0fc4d9b80792))
* **ai:** give the cli module proxy a single owner ([#1158](https://github.com/otto-nation/otto-workbench/issues/1158)) ([361cf5d](https://github.com/otto-nation/otto-workbench/commit/361cf5dcf683f3ec649cfcb61d6226f3f90a6c9b))
* **ai:** give the comment fix pass one tracking-file owner ([#996](https://github.com/otto-nation/otto-workbench/issues/996)) ([4f08107](https://github.com/otto-nation/otto-workbench/commit/4f081075c8464de152e31a8d6da3117a2d9dd0d7))
* **ai:** give the cross-cutting values one owner each ([#997](https://github.com/otto-nation/otto-workbench/issues/997)) ([cce8c4f](https://github.com/otto-nation/otto-workbench/commit/cce8c4f3b85ff49287d8fc26c396271d23c9b287))
* **ai:** give the pipeline state one owner and break the cycle ([#1012](https://github.com/otto-nation/otto-workbench/issues/1012)) ([8035c36](https://github.com/otto-nation/otto-workbench/commit/8035c364e19ec8eb450ebff9621d1c6a0ff8202b))
* **ai:** give the review subsystem its three residual owners ([#1134](https://github.com/otto-nation/otto-workbench/issues/1134)) ([d6b05e5](https://github.com/otto-nation/otto-workbench/commit/d6b05e5d353fe57086cddfeda0953cec9d0002b3))
* **ai:** give the review's vocabulary its own module ([#1013](https://github.com/otto-nation/otto-workbench/issues/1013)) ([3ad3b47](https://github.com/otto-nation/otto-workbench/commit/3ad3b472047b6b5077e1b7a58a11f23f655582e1))
* **ai:** lift shared symbols below the layer boundary ([#1123](https://github.com/otto-nation/otto-workbench/issues/1123)) ([8af5f62](https://github.com/otto-nation/otto-workbench/commit/8af5f6293c56cdb0e7f4e9d39f3c29ca13d52327))
* **ai:** move deferred-issue filing into review module ([#1324](https://github.com/otto-nation/otto-workbench/issues/1324)) ([7ac2cfd](https://github.com/otto-nation/otto-workbench/commit/7ac2cfd01b29693cf11ca83e467e5c2cbdb7de6e))
* **ai:** move legacy review adoption into a migration ([#1332](https://github.com/otto-nation/otto-workbench/issues/1332)) ([e6dcf53](https://github.com/otto-nation/otto-workbench/commit/e6dcf53b4e12b5c26e312d97b0e888271b563ece))
* **ai:** move settlement into pr/; one owner for four shared names ([#1243](https://github.com/otto-nation/otto-workbench/issues/1243)) ([75ba53b](https://github.com/otto-nation/otto-workbench/commit/75ba53bd0fa56485e7b3c3d06e8a7220aa77fc2e)), closes [#909](https://github.com/otto-nation/otto-workbench/issues/909)
* **ai:** move the comment fix pass into fix/ ([#1292](https://github.com/otto-nation/otto-workbench/issues/1292)) ([070e512](https://github.com/otto-nation/otto-workbench/commit/070e51239e39a454e66c89aa5211b0d57d4bd5f4))
* **ai:** move the fix-pass push recoveries into the landing owner ([#995](https://github.com/otto-nation/otto-workbench/issues/995)) ([40799dd](https://github.com/otto-nation/otto-workbench/commit/40799dd440f2f27fe735d979f263601d16eed52c))
* **ai:** move the rebase lifecycle into rebase/ ([#1174](https://github.com/otto-nation/otto-workbench/issues/1174)) ([50bb5d9](https://github.com/otto-nation/otto-workbench/commit/50bb5d95b3e1a31ebcb80c0434e53634a6850f00))
* **ai:** move the review lifecycle into review/; one domain writer ([#1342](https://github.com/otto-nation/otto-workbench/issues/1342)) ([7aac0a7](https://github.com/otto-nation/otto-workbench/commit/7aac0a7f4b89ce09fefd0394419e994bfc5e9bb1))
* **ai:** move thread replies into pr/; one owner for two vocabularies ([#1217](https://github.com/otto-nation/otto-workbench/issues/1217)) ([5d03b64](https://github.com/otto-nation/otto-workbench/commit/5d03b649337b4b80da0476c27abdb98f0ecf54aa))
* **ai:** move thread triage into pr/ ([#1211](https://github.com/otto-nation/otto-workbench/issues/1211)) ([830ecf5](https://github.com/otto-nation/otto-workbench/commit/830ecf5d8da9550daa9a17ea8d6b71c138cddafd))
* **ai:** name every ai/lib module through its package ([#1569](https://github.com/otto-nation/otto-workbench/issues/1569)) ([5a71453](https://github.com/otto-nation/otto-workbench/commit/5a714532902b0a112c2b32ffdb18c60831af2fe7))
* **ai:** one fix engine, and all three passes on it ([#1000](https://github.com/otto-nation/otto-workbench/issues/1000)) ([89a22c1](https://github.com/otto-nation/otto-workbench/commit/89a22c16238ac3f8fbbbc19d9ad1b94c9c386361))
* **ai:** one function runs an agent phase, whichever phase it is ([#1023](https://github.com/otto-nation/otto-workbench/issues/1023)) ([0fb1948](https://github.com/otto-nation/otto-workbench/commit/0fb1948c56a8a453c1990cc38869062b069345a4))
* **ai:** one owner for checking a review against the tree ([#1062](https://github.com/otto-nation/otto-workbench/issues/1062)) ([cf36ef6](https://github.com/otto-nation/otto-workbench/commit/cf36ef62ad97518baa95f3d4a6121e6b6e483eee))
* **ai:** one owner for finding identity, budget and orchestration ([#1106](https://github.com/otto-nation/otto-workbench/issues/1106)) ([290b9b8](https://github.com/otto-nation/otto-workbench/commit/290b9b8650840f2048ff0811d71a772e3c49d027))
* **ai:** one owner for the review document's frame ([#1032](https://github.com/otto-nation/otto-workbench/issues/1032)) ([e3f9717](https://github.com/otto-nation/otto-workbench/commit/e3f9717c8ae00aa560ec1284d07852902bc6e171))
* **ai:** one owner for the review document's metadata header ([#1028](https://github.com/otto-nation/otto-workbench/issues/1028)) ([971729a](https://github.com/otto-nation/otto-workbench/commit/971729a23c6cd843d43ed9708181038aa639c80c)), closes [#907](https://github.com/otto-nation/otto-workbench/issues/907)
* **ai:** one owner for what happens to findings across reviews ([#1068](https://github.com/otto-nation/otto-workbench/issues/1068)) ([57a92a1](https://github.com/otto-nation/otto-workbench/commit/57a92a10b74bf07f328d2468e9b746cd45658a73))
* **ai:** one owner for where a review lives on disk ([#1050](https://github.com/otto-nation/otto-workbench/issues/1050)) ([4587fe4](https://github.com/otto-nation/otto-workbench/commit/4587fe48b52c9a88dc4a61b74a591da7e96b3212))
* **ai:** one owner for where a review's sections go ([#1046](https://github.com/otto-nation/otto-workbench/issues/1046)) ([911567a](https://github.com/otto-nation/otto-workbench/commit/911567a7f9805a33a600842078794319b3b2edd6))
* **ai:** prune review_common's dead surface; share dedup helpers ([#1008](https://github.com/otto-nation/otto-workbench/issues/1008)) ([9a0e395](https://github.com/otto-nation/otto-workbench/commit/9a0e3958ece1eeb46342681b89747533e81b8ad9))
* **ai:** put the fix passes on phases behind one invocation owner ([#990](https://github.com/otto-nation/otto-workbench/issues/990)) ([28f4cd9](https://github.com/otto-nation/otto-workbench/commit/28f4cd9d233c1d3e03f287ad867242a19e684901))
* **ai:** read delegate flag arity in-process; delete --value-flags ([#1524](https://github.com/otto-nation/otto-workbench/issues/1524)) ([2871aab](https://github.com/otto-nation/otto-workbench/commit/2871aaba025d8ed45189493b801d6de72ba04255))
* **ai:** reduce pr-rebase to its entry point ([#1179](https://github.com/otto-nation/otto-workbench/issues/1179)) ([5f965ea](https://github.com/otto-nation/otto-workbench/commit/5f965ea82e81f4d45113cb3aa5f4cf3c1dec75ab))
* **ai:** reduce review-threads to its entry point ([#1329](https://github.com/otto-nation/otto-workbench/issues/1329)) ([c2202f4](https://github.com/otto-nation/otto-workbench/commit/c2202f4df9b7fb122ee7c0b20250ed8dd66790ae))
* **ai:** render the fix answer format from its parser ([#1003](https://github.com/otto-nation/otto-workbench/issues/1003)) ([bb2ee81](https://github.com/otto-nation/otto-workbench/commit/bb2ee8168dc3fc6f615f440e01a1402c01ed7b03))
* **ai:** repackage ai/lib into layers with one-way imports ([#1136](https://github.com/otto-nation/otto-workbench/issues/1136)) ([da63626](https://github.com/otto-nation/otto-workbench/commit/da6362661b25d5bf8eeb64f92cf3c28b48fa0039))
* **ai:** retire the go-task AI targets and lib/ai shell layer ([#1659](https://github.com/otto-nation/otto-workbench/issues/1659)) ([5b3ab9f](https://github.com/otto-nation/otto-workbench/commit/5b3ab9f6eab12766e6d98676e2c7d7a1c9200bdd))
* **ai:** split ci-check into owners and reduce it to a shim ([#1154](https://github.com/otto-nation/otto-workbench/issues/1154)) ([af56f77](https://github.com/otto-nation/otto-workbench/commit/af56f77ecff23537643baecd7747d789ae6e30b5))
* **ai:** split pr_context into topology, sync, and context ([#1121](https://github.com/otto-nation/otto-workbench/issues/1121)) ([24371eb](https://github.com/otto-nation/otto-workbench/commit/24371ebbead50f1242309726837717be89c629a9))
* **ai:** split reply threads and prior review into own modules ([#1112](https://github.com/otto-nation/otto-workbench/issues/1112)) ([922425a](https://github.com/otto-nation/otto-workbench/commit/922425a37dd726eb8e1eeb65d22b837b6ccb8ab3))
* **ai:** split the phase inventory out of the vocabulary ([#991](https://github.com/otto-nation/otto-workbench/issues/991)) ([25f627a](https://github.com/otto-nation/otto-workbench/commit/25f627ae20c5850205feda5a66d8cbce04f4e1ab))
* **ai:** split the summary into model, render and scope ([#1263](https://github.com/otto-nation/otto-workbench/issues/1263)) ([230aace](https://github.com/otto-nation/otto-workbench/commit/230aace581fd1360a680a2fe3ce68fda8431ed69))
* **ai:** the meta.json sidecar has one owner ([#1030](https://github.com/otto-nation/otto-workbench/issues/1030)) ([b2d4259](https://github.com/otto-nation/otto-workbench/commit/b2d42598dc8867a8430d29213d6ceafe86324cff))
* **ai:** the phase declares its prompt template ([#1016](https://github.com/otto-nation/otto-workbench/issues/1016)) ([5d7a198](https://github.com/otto-nation/otto-workbench/commit/5d7a198f4970359adacc78db2703a9fa8e80898a))
* **ai:** the phase declares whether it can be switched off ([#1018](https://github.com/otto-nation/otto-workbench/issues/1018)) ([af201a9](https://github.com/otto-nation/otto-workbench/commit/af201a99affbf5d713ce6013938f4ad62c716121))
* **ai:** the phase names its own prompt ([#1022](https://github.com/otto-nation/otto-workbench/issues/1022)) ([d98c1af](https://github.com/otto-nation/otto-workbench/commit/d98c1af769eed1168bd73bb40a86014a13fa2204))
* **ai:** the pipeline state records phases, not flags ([#1025](https://github.com/otto-nation/otto-workbench/issues/1025)) ([279fbaa](https://github.com/otto-nation/otto-workbench/commit/279fbaaa1476a6c7998a89021d6c199bf51aae12))
* **ai:** the review document answers what it says ([#1039](https://github.com/otto-nation/otto-workbench/issues/1039)) ([7d19ae8](https://github.com/otto-nation/otto-workbench/commit/7d19ae8a6487996e0f1306442394544cbe8aa210))
* **ai:** the review document owns one tally of its findings ([#1047](https://github.com/otto-nation/otto-workbench/issues/1047)) ([a40936e](https://github.com/otto-nation/otto-workbench/commit/a40936e05b8958b027dba962e7a52a3eeba35820))
* **ai:** the review's findings are read off the document ([#1042](https://github.com/otto-nation/otto-workbench/issues/1042)) ([60f8c24](https://github.com/otto-nation/otto-workbench/commit/60f8c24ee02918fff39abf1abc84030c12ee50b2))
* **ai:** type the comments thread ledger ([#948](https://github.com/otto-nation/otto-workbench/issues/948)) ([638891d](https://github.com/otto-nation/otto-workbench/commit/638891d966fb5ade9bfe791ba0ca379080515f45))
* **ai:** unify fix-pass round results and tighten review pipeline ([#1070](https://github.com/otto-nation/otto-workbench/issues/1070)) ([e188a75](https://github.com/otto-nation/otto-workbench/commit/e188a7531d494247435004ef55e96335532328f9))
* **ci:** move ci-check's body into pr.ci_check and rebase.ci_fix ([#1640](https://github.com/otto-nation/otto-workbench/issues/1640)) ([91f14b3](https://github.com/otto-nation/otto-workbench/commit/91f14b3482f456e97d826dfd6e9a31e5df8c30f1))
* **claude:** convert the four Claude hooks to shims ([#1616](https://github.com/otto-nation/otto-workbench/issues/1616)) ([5a2ce76](https://github.com/otto-nation/otto-workbench/commit/5a2ce763a6805d8eb497a72a8fef942c99f9812b))
* **claude:** move project scaffolding out of steps.sh ([#1603](https://github.com/otto-nation/otto-workbench/issues/1603)) ([0525121](https://github.com/otto-nation/otto-workbench/commit/0525121adf1e5e4fd0f1615982caf35c8a2aa57e))
* **cli:** use sync_header for the migration phase label ([#1212](https://github.com/otto-nation/otto-workbench/issues/1212)) ([1d8e784](https://github.com/otto-nation/otto-workbench/commit/1d8e784c66c62d8927269456b35cefa80edba689))
* **config:** name issues.base_url for the instance, not the vendor ([#1429](https://github.com/otto-nation/otto-workbench/issues/1429)) ([e99491f](https://github.com/otto-nation/otto-workbench/commit/e99491f10a29726b8354ed57668945a973909fc7))
* **config:** one owner each for what the config is, shows, and writes ([#1115](https://github.com/otto-nation/otto-workbench/issues/1115)) ([02b5521](https://github.com/otto-nation/otto-workbench/commit/02b55219e52b52494991ee247e55efd7521a98ea))
* **config:** rename the issue_tracker section to issues ([#1200](https://github.com/otto-nation/otto-workbench/issues/1200)) ([09409af](https://github.com/otto-nation/otto-workbench/commit/09409afca6aff34eee6b8ed954623d78b0eb31fd))
* **config:** resolve config scopes through one owner ([#1014](https://github.com/otto-nation/otto-workbench/issues/1014)) ([de9bf27](https://github.com/otto-nation/otto-workbench/commit/de9bf272c1793125fed9f85a33f022f0f1ddaf42))
* **dream:** drop the unused session_counts helper ([#1369](https://github.com/otto-nation/otto-workbench/issues/1369)) ([18af7bf](https://github.com/otto-nation/otto-workbench/commit/18af7bf27091f0d39548e2d3f09a533d63faeb87))
* **eval:** convert eval-models and rules-canary to shims ([#1651](https://github.com/otto-nation/otto-workbench/issues/1651)) ([6dd7ebd](https://github.com/otto-nation/otto-workbench/commit/6dd7ebdb01b69eb076e84d2ec77e30d1e731e0a8))
* **fix:** split the verify gate out; index its chunk artifacts ([#1521](https://github.com/otto-nation/otto-workbench/issues/1521)) ([62a8149](https://github.com/otto-nation/otto-workbench/commit/62a8149dcfd9d78bbde5c059eb15ada2ff1d779c))
* **gh:** give gh one client instead of 45 call sites ([#934](https://github.com/otto-nation/otto-workbench/issues/934)) ([2832db2](https://github.com/otto-nation/otto-workbench/commit/2832db24e369f5751415c8abdc5bbdc8fe19fdeb))
* **gh:** name the thread refetch page size; correct two stale docstrings ([#1398](https://github.com/otto-nation/otto-workbench/issues/1398)) ([c5f86a8](https://github.com/otto-nation/otto-workbench/commit/c5f86a844737456ad294d18cb29ac5a3504bee76))
* **gh:** split pr_reads into pr_pages, pr_data and pr_reads ([#1602](https://github.com/otto-nation/otto-workbench/issues/1602)) ([d6f33a4](https://github.com/otto-nation/otto-workbench/commit/d6f33a4983f23030b6b79ef381a51692c87a3b9d))
* **git:** resolve the shared git dir through one owner ([#1064](https://github.com/otto-nation/otto-workbench/issues/1064)) ([39e501c](https://github.com/otto-nation/otto-workbench/commit/39e501c8ebdc5a90f609c935f00c4095520777b3))
* **lib:** publish one ~/.env.local reader for both harnesses ([#1213](https://github.com/otto-nation/otto-workbench/issues/1213)) ([92bc8d4](https://github.com/otto-nation/otto-workbench/commit/92bc8d402e7ec8a504b4332dab32cda11d160856))
* **mcp:** discover tools from the registry, not by probing scripts ([#1559](https://github.com/otto-nation/otto-workbench/issues/1559)) ([eb2b8cc](https://github.com/otto-nation/otto-workbench/commit/eb2b8cc72493b2f6d9f4b7e208d00134df5f065b))
* **memory:** convert dream-scan and promote-scan to shims ([#1653](https://github.com/otto-nation/otto-workbench/issues/1653)) ([7c0495a](https://github.com/otto-nation/otto-workbench/commit/7c0495a3d2d6e96ae83c54ae415509e31be81e04)), closes [#911](https://github.com/otto-nation/otto-workbench/issues/911)
* **pi:** file the review guard under the Pi layer that owns it ([#1109](https://github.com/otto-nation/otto-workbench/issues/1109)) ([143b94c](https://github.com/otto-nation/otto-workbench/commit/143b94cb708e0ac7d4b41048ac3d54a2bc57b50b))
* **pr:** give each Action-cell wording one owner ([#1315](https://github.com/otto-nation/otto-workbench/issues/1315)) ([88ca3a7](https://github.com/otto-nation/otto-workbench/commit/88ca3a728649e726af986595d7acb8ba44495839))
* **pr:** give the comment anchor vocabulary one owner ([#1314](https://github.com/otto-nation/otto-workbench/issues/1314)) ([621758d](https://github.com/otto-nation/otto-workbench/commit/621758d3760699d8c74068bf55323549c7b2a1c5))
* **pr:** give the PR template one resolver ([#1477](https://github.com/otto-nation/otto-workbench/issues/1477)) ([090cde4](https://github.com/otto-nation/otto-workbench/commit/090cde400915add40577d5a70a194dc82f6e5488))
* **pr:** give the triage vocabulary one owner ([#1308](https://github.com/otto-nation/otto-workbench/issues/1308)) ([a0283cd](https://github.com/otto-nation/otto-workbench/commit/a0283cd8b7db785346778097d71a554adb5de706))
* **pr:** give two pieces of derived state their owner ([#1388](https://github.com/otto-nation/otto-workbench/issues/1388)) ([b2546d1](https://github.com/otto-nation/otto-workbench/commit/b2546d1968fa1c7681506ac272e9481dfc8dc52a))
* **pr:** let each domain render and judge itself ([#945](https://github.com/otto-nation/otto-workbench/issues/945)) ([a3db904](https://github.com/otto-nation/otto-workbench/commit/a3db9048aff82eb06d1666203afc53365f3cb5da))
* **pr:** move the describe pass into pr.describe ([#1637](https://github.com/otto-nation/otto-workbench/issues/1637)) ([4af9d26](https://github.com/otto-nation/otto-workbench/commit/4af9d263ea070548e878dc8ff86591b14f024b7a))
* **pr:** one owner for GH_TOKEN resolution in pr.gh_token ([#1588](https://github.com/otto-nation/otto-workbench/issues/1588)) ([532c5c3](https://github.com/otto-nation/otto-workbench/commit/532c5c3453fc61a2a9650fca52417402d48752ea))
* **pr:** re-arm fix flags through their owner ([#1504](https://github.com/otto-nation/otto-workbench/issues/1504)) ([2e539e3](https://github.com/otto-nation/otto-workbench/commit/2e539e3c48d04ecc19808903d6b72437fa274523)), closes [#1502](https://github.com/otto-nation/otto-workbench/issues/1502)
* **pr:** record published rows and replies in the comment itself ([#1575](https://github.com/otto-nation/otto-workbench/issues/1575)) ([8233164](https://github.com/otto-nation/otto-workbench/commit/8233164e09361f5c6b2403fb7ec5099897392b54))
* **pr:** split the PR domains out of pr_state ([#939](https://github.com/otto-nation/otto-workbench/issues/939)) ([9e09fff](https://github.com/otto-nation/otto-workbench/commit/9e09fff27507c42b67b6c3625bf3b77e68996600))
* **rebase:** drive pre-push fixes through the fix engine ([#1184](https://github.com/otto-nation/otto-workbench/issues/1184)) ([910bbc9](https://github.com/otto-nation/otto-workbench/commit/910bbc9f5c4a15ce7e3d717bd2ea396dfaba5873))
* **rebase:** move the pr rebase commands into rebase.commands ([#1638](https://github.com/otto-nation/otto-workbench/issues/1638)) ([58d397e](https://github.com/otto-nation/otto-workbench/commit/58d397e003290b9ebd7b4a87977ef59fec85b77a))
* **rebase:** pr-rebase adopts git_client and the land owner ([#1011](https://github.com/otto-nation/otto-workbench/issues/1011)) ([86610db](https://github.com/otto-nation/otto-workbench/commit/86610db2c6766a34465bdf5020cd3efe6d1927f3))
* **registries:** declare model env vars once for both harnesses ([#1228](https://github.com/otto-nation/otto-workbench/issues/1228)) ([aa2112b](https://github.com/otto-nation/otto-workbench/commit/aa2112b3d22884bbe2b75160320a34d82146e0e4))
* **retro:** convert retro-scan and retro-consume to shims ([#1612](https://github.com/otto-nation/otto-workbench/issues/1612)) ([6730434](https://github.com/otto-nation/otto-workbench/commit/6730434bddff355d7233a17f542139e42d67b443))
* **review:** drop fit_files' unused deprioritise parameter ([#1457](https://github.com/otto-nation/otto-workbench/issues/1457)) ([c9b54f5](https://github.com/otto-nation/otto-workbench/commit/c9b54f52a22066a09407dbfecc2959291b1cb853))
* **review:** harness-owned frontmatter; rename claude-review ([#1560](https://github.com/otto-nation/otto-workbench/issues/1560)) ([847cedd](https://github.com/otto-nation/otto-workbench/commit/847ceddb44be721c81b8655b72ac4cd610f76b04))
* **review:** move pr comments' thread flow into review.comment_threads ([#1644](https://github.com/otto-nation/otto-workbench/issues/1644)) ([4d473a9](https://github.com/otto-nation/otto-workbench/commit/4d473a95c3c6a20b9e97e8bea467b0b70505c6cc))
* **review:** move review-orchestrate's run into review.orchestrate ([#1652](https://github.com/otto-nation/otto-workbench/issues/1652)) ([7ce05fb](https://github.com/otto-nation/otto-workbench/commit/7ce05fbad7503a2768be62f34936758a8948bf45))
* **review:** move review-positions and review-rebuild bodies into review ([#1621](https://github.com/otto-nation/otto-workbench/issues/1621)) ([eeb2db0](https://github.com/otto-nation/otto-workbench/commit/eeb2db0783de7d66505268538ab8705197ff1fc0)), closes [#911](https://github.com/otto-nation/otto-workbench/issues/911)
* **review:** move review-post's body into review.post_file ([#1643](https://github.com/otto-nation/otto-workbench/issues/1643)) ([54d9d18](https://github.com/otto-nation/otto-workbench/commit/54d9d184653fe371f2169f9f665721acf429535c))
* **review:** unify the PR and self review flows behind one runner ([#1350](https://github.com/otto-nation/otto-workbench/issues/1350)) ([bcc9b51](https://github.com/otto-nation/otto-workbench/commit/bcc9b51ffecd7627cc6292aa7bac4affddddf1f1))
* **site:** move the design system to @otto-nation/brand ([#928](https://github.com/otto-nation/otto-workbench/issues/928)) ([286e64d](https://github.com/otto-nation/otto-workbench/commit/286e64dcf9f0c4d918cb5090978f8c3bba68a422))
* **skills:** drop debugger's skip: field, which nothing reads ([#1496](https://github.com/otto-nation/otto-workbench/issues/1496)) ([3283a44](https://github.com/otto-nation/otto-workbench/commit/3283a44901a5030d0d6651dd967f34423c944e27))
* **task:** retire the task --global wrapper and the task component ([#1661](https://github.com/otto-nation/otto-workbench/issues/1661)) ([760bf7e](https://github.com/otto-nation/otto-workbench/commit/760bf7e239269366dc31281188aeb569d931bbb4))
* **trail:** convert otto-log and ai-usage-log to shims ([#1614](https://github.com/otto-nation/otto-workbench/issues/1614)) ([6c28ea3](https://github.com/otto-nation/otto-workbench/commit/6c28ea3750801b4321c1f8905d121b6ec959ec32))
* **workbench:** split otto-workbench's logic into lib/ ([#1610](https://github.com/otto-nation/otto-workbench/issues/1610)) ([b6e1cf3](https://github.com/otto-nation/otto-workbench/commit/b6e1cf363038db1fd2d267344b796ffb63e7ffaf))

## [1.47.0](https://github.com/otto-nation/otto-workbench/compare/v1.46.5...v1.47.0) (2026-07-31)


### Features

* **ci-check:** --wait mode with incremental reporting; fix log fallback ([#562](https://github.com/otto-nation/otto-workbench/issues/562)) ([57bfa11](https://github.com/otto-nation/otto-workbench/commit/57bfa11fb862365fb9c8bfa466a753b0959b1960))
* **review:** add verdict and status fields to post.jsonl ([#566](https://github.com/otto-nation/otto-workbench/issues/566)) ([7c58afd](https://github.com/otto-nation/otto-workbench/commit/7c58afd773ea55f9fcf35dc4bc46345a1bac792f))
* **review:** static analysis framework for review pipeline ([#565](https://github.com/otto-nation/otto-workbench/issues/565)) ([00de37f](https://github.com/otto-nation/otto-workbench/commit/00de37f98847ee3c721cd595e5526f39e823848c))


### Bug Fixes

* **pr-rebase:** handle detached HEAD worktrees ([#567](https://github.com/otto-nation/otto-workbench/issues/567)) ([bf6907f](https://github.com/otto-nation/otto-workbench/commit/bf6907fdd0a7e2e252ea96b7f2edbaad26352bd1))

## [1.46.5](https://github.com/otto-nation/otto-workbench/compare/v1.46.4...v1.46.5) (2026-07-28)


### Bug Fixes

* **review:** accurate cost, token, and duration tracking ([#557](https://github.com/otto-nation/otto-workbench/issues/557)) ([40f746d](https://github.com/otto-nation/otto-workbench/commit/40f746d4905de2af1047a44152f24d75fd4c2041))
* **review:** sonnet-only pipeline to prevent rate limiting ([#561](https://github.com/otto-nation/otto-workbench/issues/561)) ([c00121d](https://github.com/otto-nation/otto-workbench/commit/c00121d55954f6349ce5971d415cff675bccbb97))


### Performance Improvements

* **tests:** optimize slow bats test suite ([#560](https://github.com/otto-nation/otto-workbench/issues/560)) ([dd89604](https://github.com/otto-nation/otto-workbench/commit/dd896040f67e25399cdd208efe61292a57192843))

## [1.46.4](https://github.com/otto-nation/otto-workbench/compare/v1.46.3...v1.46.4) (2026-07-27)


### Bug Fixes

* **pr-rebase:** auto-fix pre-push check failures after conflict resolution ([#553](https://github.com/otto-nation/otto-workbench/issues/553)) ([0bc93a0](https://github.com/otto-nation/otto-workbench/commit/0bc93a08fccbee1b7acc039a6953cb2ec57c214d))
* **review:** fall back to sonnet on opus quota exhaustion ([#554](https://github.com/otto-nation/otto-workbench/issues/554)) ([554b737](https://github.com/otto-nation/otto-workbench/commit/554b737975179e6652b4596be0a33d04cab998ca))

## [1.46.3](https://github.com/otto-nation/otto-workbench/compare/v1.46.2...v1.46.3) (2026-07-24)


### Bug Fixes

* **pr-rebase:** auto-resolve generated files instead of AI resolution ([#547](https://github.com/otto-nation/otto-workbench/issues/547)) ([cc5da99](https://github.com/otto-nation/otto-workbench/commit/cc5da99398d40ba70b221a39e9a117764d26c287))
* **review:** improve pipeline resilience and failure observability ([#550](https://github.com/otto-nation/otto-workbench/issues/550)) ([14b810d](https://github.com/otto-nation/otto-workbench/commit/14b810d92beadaf2b242290f0c1fb3e45f4cd7d6))

## [1.46.2](https://github.com/otto-nation/otto-workbench/compare/v1.46.1...v1.46.2) (2026-07-24)


### Bug Fixes

* **pr-comments:** add permalinks for comment items and reviewer column in summary ([#544](https://github.com/otto-nation/otto-workbench/issues/544)) ([f0d98f5](https://github.com/otto-nation/otto-workbench/commit/f0d98f5973d95b8927e226d669d06080d8017e7b))

## [1.46.1](https://github.com/otto-nation/otto-workbench/compare/v1.46.0...v1.46.1) (2026-07-24)


### Bug Fixes

* **review:** retry synthesis on transient API errors; detect self-review fallback ([#541](https://github.com/otto-nation/otto-workbench/issues/541)) ([89b02d9](https://github.com/otto-nation/otto-workbench/commit/89b02d92f79f5a227d8128096a5cd372c7fa3bbb))

## [1.46.0](https://github.com/otto-nation/otto-workbench/compare/v1.45.2...v1.46.0) (2026-07-24)


### Features

* **ci-check:** improve extraction robustness and artifact fallback ([#539](https://github.com/otto-nation/otto-workbench/issues/539)) ([55f93a8](https://github.com/otto-nation/otto-workbench/commit/55f93a83792b9d6b339490d8e18e4b91673d1e77))
* **ci-check:** rebase onto main before fixing CI failures ([#526](https://github.com/otto-nation/otto-workbench/issues/526)) ([1a74710](https://github.com/otto-nation/otto-workbench/commit/1a747104550c3188de022e97a7c89e42d7fd1223))
* **rules:** apply retro proposals — rename audit, deployment ordering, error handling, CI depth ([#532](https://github.com/otto-nation/otto-workbench/issues/532)) ([0444ad4](https://github.com/otto-nation/otto-workbench/commit/0444ad4ba34940089031743acda5b83371023428))


### Bug Fixes

* **ci-check:** report in-progress runs instead of false success ([#531](https://github.com/otto-nation/otto-workbench/issues/531)) ([9da1d6a](https://github.com/otto-nation/otto-workbench/commit/9da1d6a203e79ac4c3b3f7ea67d9df51d96bd367))
* **pr-comments:** include issue link in deferred summary rows ([#534](https://github.com/otto-nation/otto-workbench/issues/534)) ([2aa10ff](https://github.com/otto-nation/otto-workbench/commit/2aa10ffd06956632a9ae45e69743a02fa807bdec))
* **pr-comments:** remove false-positive reconciliation; defer replies until --resolve ([#523](https://github.com/otto-nation/otto-workbench/issues/523)) ([a94bec5](https://github.com/otto-nation/otto-workbench/commit/a94bec5e574fc77128d07d8c450052320e4e87d4))
* **pr-comments:** remove file-level reconciliation that falsely resolves threads ([#540](https://github.com/otto-nation/otto-workbench/issues/540)) ([55993a6](https://github.com/otto-nation/otto-workbench/commit/55993a6060d3e3e42f000c41ab48a9708095799c))
* **pr:** prefer resolved PR number over --branch in delegate dispatch ([#538](https://github.com/otto-nation/otto-workbench/issues/538)) ([2d827f1](https://github.com/otto-nation/otto-workbench/commit/2d827f1fcf81d296848ec8f933104f438c69ee2a))
* **review-post:** re-verify inline positions on SHA drift instead of falling back to comment ([#527](https://github.com/otto-nation/otto-workbench/issues/527)) ([7f8479a](https://github.com/otto-nation/otto-workbench/commit/7f8479a592873c8186ee748a5d6b779f196fb75f))


### Code Refactoring

* **pr-comments:** consolidate thread model types ([#537](https://github.com/otto-nation/otto-workbench/issues/537)) ([48c4363](https://github.com/otto-nation/otto-workbench/commit/48c43632150eb743bd816870a60d2a6325f23757))

## [1.45.2](https://github.com/otto-nation/otto-workbench/compare/v1.45.1...v1.45.2) (2026-07-21)


### Bug Fixes

* **review:** strip bold-wrapped verdict action prefix before posting ([#520](https://github.com/otto-nation/otto-workbench/issues/520)) ([58cdd25](https://github.com/otto-nation/otto-workbench/commit/58cdd2556bd7223ab365fbeda996451defc883d5))

## [1.45.1](https://github.com/otto-nation/otto-workbench/compare/v1.45.0...v1.45.1) (2026-07-21)


### Bug Fixes

* **pr-comments:** post replies for already-addressed threads ([#519](https://github.com/otto-nation/otto-workbench/issues/519)) ([a934e04](https://github.com/otto-nation/otto-workbench/commit/a934e043b037192a91a3bbcdafa7a0801775292f))
* **review:** default _confirm to False when stdin is not interactive ([#516](https://github.com/otto-nation/otto-workbench/issues/516)) ([f6e1cdc](https://github.com/otto-nation/otto-workbench/commit/f6e1cdc19123760fd19d9a72377b35954453c70d))

## [1.45.0](https://github.com/otto-nation/otto-workbench/compare/v1.44.0...v1.45.0) (2026-07-21)


### Features

* **ci-failures:** auto-fix without confirmation and enrich BUILD failure context ([#501](https://github.com/otto-nation/otto-workbench/issues/501)) ([f4fc928](https://github.com/otto-nation/otto-workbench/commit/f4fc928fbe84530df0a8a82d72b33bb2e63e3ed9))
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
* **rules:** add --repo-dir flag guidance to self-review rule ([#497](https://github.com/otto-nation/otto-workbench/issues/497)) ([4e46c38](https://github.com/otto-nation/otto-workbench/commit/4e46c382c778e249764ac5fe8d18355bd1578633))
* **settings:** add permission for skill scripts; document $VAR expansion ([#514](https://github.com/otto-nation/otto-workbench/issues/514)) ([8f9ada7](https://github.com/otto-nation/otto-workbench/commit/8f9ada760ae0875d96f3eb80e6ada60473c489ea))
* **validate-nesting:** lower Go default max depth from 3 to 2 ([#512](https://github.com/otto-nation/otto-workbench/issues/512)) ([4db9b7d](https://github.com/otto-nation/otto-workbench/commit/4db9b7d1a40056adf071865711d3b26a8ccea789))

## [1.44.0](https://github.com/otto-nation/otto-workbench/compare/v1.43.0...v1.44.0) (2026-07-16)


### Features

* **review-post:** add summary/verdict to body and improve nit formatting ([#496](https://github.com/otto-nation/otto-workbench/issues/496)) ([d5dfb1a](https://github.com/otto-nation/otto-workbench/commit/d5dfb1afa37994ba814285b0cb0ddcd5f6c10bc7))


### Bug Fixes

* **maintenance:** run sync unconditionally ([#492](https://github.com/otto-nation/otto-workbench/issues/492)) ([ffec584](https://github.com/otto-nation/otto-workbench/commit/ffec5849d98483d48ddb2112bd4d8b5ff5ebd4e3))
* **pr-comments:** handle AI preamble text before JSON in triage output ([#494](https://github.com/otto-nation/otto-workbench/issues/494)) ([687ab02](https://github.com/otto-nation/otto-workbench/commit/687ab02de9f003c290a42fb46d5c486974a5f2fa))

## [1.43.0](https://github.com/otto-nation/otto-workbench/compare/v1.42.0...v1.43.0) (2026-07-15)


### Features

* **ci-check:** extract failed step name, add drift log markers ([#491](https://github.com/otto-nation/otto-workbench/issues/491)) ([4747fb7](https://github.com/otto-nation/otto-workbench/commit/4747fb791d80ac16f1308388c5c633eb262cdc31))
* **pr-comments:** deferred thread tracking, issue lifecycle, and thread resolution ([#488](https://github.com/otto-nation/otto-workbench/issues/488)) ([c0fc5b8](https://github.com/otto-nation/otto-workbench/commit/c0fc5b81dbd0e9b14729f2224b3c00c8c069cd50))
* **registries:** validate and render commands field ([#490](https://github.com/otto-nation/otto-workbench/issues/490)) ([4f6d5fc](https://github.com/otto-nation/otto-workbench/commit/4f6d5fc779a665ee17754f51066a3144aa89350b))
* **review:** integrate PR state and role awareness into review prompts ([#489](https://github.com/otto-nation/otto-workbench/issues/489)) ([6d0dfc2](https://github.com/otto-nation/otto-workbench/commit/6d0dfc2d5137e00c5c4d7955f30a4fc72850dfbf))


### Bug Fixes

* **pr-comments:** recover agent commit SHA when script commit fails ([#486](https://github.com/otto-nation/otto-workbench/issues/486)) ([fdf2c33](https://github.com/otto-nation/otto-workbench/commit/fdf2c3388cd0d529088bf02b0ce7538295653b9f))
* **review:** skip incremental delta when prior SHA equals HEAD ([#487](https://github.com/otto-nation/otto-workbench/issues/487)) ([517a3d5](https://github.com/otto-nation/otto-workbench/commit/517a3d591001c491e43ab570e0c2dbb8c5fceb91))
* **review:** stop printing JSON summary to stdout, suppress false incomplete warnings ([#483](https://github.com/otto-nation/otto-workbench/issues/483)) ([d9edafb](https://github.com/otto-nation/otto-workbench/commit/d9edafb06b903e2cc7d511b0c4956fe005b23017))

## [1.42.0](https://github.com/otto-nation/otto-workbench/compare/v1.41.2...v1.42.0) (2026-07-14)


### Features

* **review:** show findings, verdict, and phase warnings in summary ([#481](https://github.com/otto-nation/otto-workbench/issues/481)) ([26c68f2](https://github.com/otto-nation/otto-workbench/commit/26c68f239723527ecf545782351bae32b80a69da))


### Bug Fixes

* **pr-context:** skip update_to_remote when worktree is on a different branch ([#475](https://github.com/otto-nation/otto-workbench/issues/475)) ([5db16f5](https://github.com/otto-nation/otto-workbench/commit/5db16f583b40569b2ce9ac02554af01a5f98a467))
* **pr:** handle SIGINT to prevent traceback on Ctrl+C ([#478](https://github.com/otto-nation/otto-workbench/issues/478)) ([a87522b](https://github.com/otto-nation/otto-workbench/commit/a87522b6ce51c974c08046321f2352a4d5ede7fc))
* **review:** log issue detection attempts before prompting ([#480](https://github.com/otto-nation/otto-workbench/issues/480)) ([8ee3401](https://github.com/otto-nation/otto-workbench/commit/8ee3401a1815d98d94c97c8f922f5f9423a6223e))
* **review:** mark review as error when group agents fail ([#482](https://github.com/otto-nation/otto-workbench/issues/482)) ([c1cf672](https://github.com/otto-nation/otto-workbench/commit/c1cf672d9a0e5a3edb4827a9f19ce126383281b3))

## [1.41.2](https://github.com/otto-nation/otto-workbench/compare/v1.41.1...v1.41.2) (2026-07-13)


### Bug Fixes

* **review-threads:** commit regenerated files and surface non-inline comments ([#473](https://github.com/otto-nation/otto-workbench/issues/473)) ([0260dd7](https://github.com/otto-nation/otto-workbench/commit/0260dd75ade1ad3432b0afe3aebe59af500cc040))

## [1.41.1](https://github.com/otto-nation/otto-workbench/compare/v1.41.0...v1.41.1) (2026-07-13)


### Bug Fixes

* **comments:** surface review-level body comments in pr-comments ([#472](https://github.com/otto-nation/otto-workbench/issues/472)) ([41b471b](https://github.com/otto-nation/otto-workbench/commit/41b471bc20c500ff51cf400f9149b4ee041b6164))
* **rebase:** include base-side context for AI conflict resolution ([#471](https://github.com/otto-nation/otto-workbench/issues/471)) ([121cfd7](https://github.com/otto-nation/otto-workbench/commit/121cfd72d2d3d561b96ab264a76ebafcecd1ab1d))
* **review:** classify positional targets as PR or branch before resolving context ([#466](https://github.com/otto-nation/otto-workbench/issues/466)) ([060e2a3](https://github.com/otto-nation/otto-workbench/commit/060e2a3e2134548f14b5d50add7fb9765aedd842))


### Performance Improvements

* **tests:** use setup_file() to reduce test suite runtime by ~37s ([#469](https://github.com/otto-nation/otto-workbench/issues/469)) ([72be09d](https://github.com/otto-nation/otto-workbench/commit/72be09d9229350b09b7f4c2b3fc099d185ee03a9))

## [1.41.0](https://github.com/otto-nation/otto-workbench/compare/v1.40.0...v1.41.0) (2026-07-10)


### Features

* **review:** separate cache tokens from fresh in usage summary ([#464](https://github.com/otto-nation/otto-workbench/issues/464)) ([9d5f08e](https://github.com/otto-nation/otto-workbench/commit/9d5f08ef018f29d6796e35620e73e3d70e5e1392))
* **review:** set review status to error when synthesis agent fails ([#459](https://github.com/otto-nation/otto-workbench/issues/459)) ([15e1b49](https://github.com/otto-nation/otto-workbench/commit/15e1b491dd992bcb16417b40cfdb5d9ba806c36e))


### Bug Fixes

* **review:** inject custom agent definitions in --bare mode ([#462](https://github.com/otto-nation/otto-workbench/issues/462)) ([b0e003f](https://github.com/otto-nation/otto-workbench/commit/b0e003f79ac2fd17ecb4963752c4f7df93ab0c10))
* **review:** stop auto-injecting --self for branch positionals and add review discovery fallback ([#463](https://github.com/otto-nation/otto-workbench/issues/463)) ([1d14547](https://github.com/otto-nation/otto-workbench/commit/1d145479e5918b10a6d842b7e96813dae4fea740))

## [1.40.0](https://github.com/otto-nation/otto-workbench/compare/v1.39.0...v1.40.0) (2026-07-09)


### Features

* **pr:** fetch and reset worktree to remote before pr commands ([#456](https://github.com/otto-nation/otto-workbench/issues/456)) ([5beede8](https://github.com/otto-nation/otto-workbench/commit/5beede8b8327f7a399dfd71f335b3b5f5e505060))
* **review:** add lead scout, disprove gate, and review profiles ([#458](https://github.com/otto-nation/otto-workbench/issues/458)) ([ffbe6d2](https://github.com/otto-nation/otto-workbench/commit/ffbe6d238c5ba49bd53e03ac86685b4aa741face))


### Bug Fixes

* **pr-comments:** track seen issue-level discussion comments in state ([#453](https://github.com/otto-nation/otto-workbench/issues/453)) ([ef75eb5](https://github.com/otto-nation/otto-workbench/commit/ef75eb5403366510eb7f3f17cb0071a697ff1c6d))
* **pr:** prevent --self injection when PR target comes from global flag or context ([#457](https://github.com/otto-nation/otto-workbench/issues/457)) ([52f19c0](https://github.com/otto-nation/otto-workbench/commit/52f19c00eb9253a2cc355ce52ce91d07f40a2cf7))

## [1.39.0](https://github.com/otto-nation/otto-workbench/compare/v1.38.0...v1.39.0) (2026-07-02)


### Features

* **ai:** add --effort and --max-groups flags to claude-review ([#442](https://github.com/otto-nation/otto-workbench/issues/442)) ([313bf9a](https://github.com/otto-nation/otto-workbench/commit/313bf9a1c650b07b97ebd609c87a5b084aa4b2a6))
* **ai:** add reviewer-lite agent for group/angles/fix phases ([#447](https://github.com/otto-nation/otto-workbench/issues/447)) ([5a6bfc6](https://github.com/otto-nation/otto-workbench/commit/5a6bfc6e143f4e96b7cb3278216ca056409a6eae))
* **ai:** prefer merging review groups with shared directory prefix ([#451](https://github.com/otto-nation/otto-workbench/issues/451)) ([8f1a502](https://github.com/otto-nation/otto-workbench/commit/8f1a50297ca4335e8f65422101bc2717a1cf5602))
* **ai:** scope delta, reply threads, and PR header per group ([#443](https://github.com/otto-nation/otto-workbench/issues/443)) ([8fe5693](https://github.com/otto-nation/otto-workbench/commit/8fe56930f35e518daad587f712bad40cc4de5f1b))


### Bug Fixes

* **ai:** improve pr-rebase conflict resolution parse diagnostics ([#440](https://github.com/otto-nation/otto-workbench/issues/440)) ([61b6868](https://github.com/otto-nation/otto-workbench/commit/61b6868f25067a14cba08e19caaa9442e85ec2a8))


### Code Refactoring

* **ai:** reduce post-processing file re-reads to single read/write ([#452](https://github.com/otto-nation/otto-workbench/issues/452)) ([9d05339](https://github.com/otto-nation/otto-workbench/commit/9d05339e564eab22d788766239321542c36c254f))

## [1.38.0](https://github.com/otto-nation/otto-workbench/compare/v1.37.3...v1.38.0) (2026-06-30)


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
* **ai:** track source_run_id per failure in ci-check multi-run merging ([#434](https://github.com/otto-nation/otto-workbench/issues/434)) ([7e3435b](https://github.com/otto-nation/otto-workbench/commit/7e3435b2830eb6734e5f8ca94f08d602d5884cd1))
* **rules:** add branch analysis rule; fix two-dot diff bug ([#418](https://github.com/otto-nation/otto-workbench/issues/418)) ([c258e9c](https://github.com/otto-nation/otto-workbench/commit/c258e9c6e55b016a11d61da9968897a0e6c7fcde))
* **state:** detect sub-tool drift; fix AI sync dispatch ([#422](https://github.com/otto-nation/otto-workbench/issues/422)) ([dc3c6d8](https://github.com/otto-nation/otto-workbench/commit/dc3c6d820c834dff96a19b593f46874464fa4846))


### Code Refactoring

* **ai:** rename context skill and file to architecture ([#420](https://github.com/otto-nation/otto-workbench/issues/420)) ([1908959](https://github.com/otto-nation/otto-workbench/commit/190895900a6618f87bad8582d3a44b9883b71084))
* **ai:** restructure rules with decision ladders; remove system-prompt overlap ([#425](https://github.com/otto-nation/otto-workbench/issues/425)) ([3c0756e](https://github.com/otto-nation/otto-workbench/commit/3c0756efed3208f7a1232479d86a305fb3f8d805))

## [1.37.3](https://github.com/otto-nation/otto-workbench/compare/v1.37.2...v1.37.3) (2026-06-29)


### Bug Fixes

* **pr-rebase:** auto-stash dirty tree; stage all tidy changes; abort on continue failure ([#408](https://github.com/otto-nation/otto-workbench/issues/408)) ([15572c6](https://github.com/otto-nation/otto-workbench/commit/15572c6912103d7dccd69d53a14e25bad0b1ba4c))
* self-review findings ([#412](https://github.com/otto-nation/otto-workbench/issues/412)) ([0569472](https://github.com/otto-nation/otto-workbench/commit/0569472cccb6fa3207ff4ea2bd9651844b54c0fe))


### Code Refactoring

* redirect tool events to stderr; misc cleanups ([#411](https://github.com/otto-nation/otto-workbench/issues/411)) ([55e85bd](https://github.com/otto-nation/otto-workbench/commit/55e85bd058a01a4b36ee68911f20d783e67e7421))
* **retro:** extract helpers; scope cleanup to consumed reviews ([#413](https://github.com/otto-nation/otto-workbench/issues/413)) ([57857b9](https://github.com/otto-nation/otto-workbench/commit/57857b9e6ed1c5780efc5322686709b086b10cc1))

## [1.37.2](https://github.com/otto-nation/otto-workbench/compare/v1.37.1...v1.37.2) (2026-06-29)


### Code Refactoring

* **pr-rebase:** replace fragmented resume logic with _drive_to_completion loop ([#405](https://github.com/otto-nation/otto-workbench/issues/405)) ([e0d0046](https://github.com/otto-nation/otto-workbench/commit/e0d0046c3e01a278b444841d1dd521d05513bf4c))

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
