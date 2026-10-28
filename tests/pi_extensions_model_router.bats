#!/usr/bin/env bats
# Tests for the model-router Pi extension: which tier a workbench/auto request goes to.
# select-on: ai/pi/extensions/model-router/* ai/pi/extensions/_shared/workspace.ts

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_TEST_TMPDIR/node-compile-cache"
  ROUTER="$REPO_ROOT/ai/pi/extensions/model-router"
}

teardown() {
  common_teardown
}

# _decide INPUT_JSON — decide() on INPUT_JSON, printed as JSON. `planFiles` lists
# the paths the plan predicate answers yes for; route.ts imports nothing, so
# node loads it directly.
_decide() {
  node --input-type=module -e "
    const { decide } = await import('$ROUTER/route.ts');
    const input = JSON.parse(process.argv[1]);
    const plans = new Set(input.planFiles ?? []);
    const decision = decide({
      reason: input.reason, state: input.state, messages: input.messages ?? [],
      hasPrevious: input.hasPrevious ?? false, hasFailed: input.hasFailed ?? false,
      isPlanFile: (path) => plans.has(path),
    });
    console.log(JSON.stringify(decision));
  " "$1"
}

# Message fixtures, in the shapes Pi sends.
SKILL_COMMAND='{"role":"user","content":"<skill name=\"brainstorming\" location=\"/s/brainstorming/SKILL.md\">\nbody\n</skill>"}'
SKILL_READ='{"role":"assistant","content":[{"type":"toolCall","id":"r1","name":"read","arguments":{"path":"/home/u/.agents/skills/writing-plans/SKILL.md"}}]}'
OTHER_SKILL='{"role":"user","content":"<skill name=\"pdf-tools\" location=\"/s/pdf-tools/SKILL.md\">x</skill>"}'
EDIT_CODE='{"role":"assistant","content":[{"type":"toolCall","id":"e1","name":"edit","arguments":{"path":"src/app.py"}}]}'
EDIT_CODE_OK='{"role":"toolResult","toolCallId":"e1","isError":false,"content":[]}'
EDIT_CODE_FAILED='{"role":"toolResult","toolCallId":"e1","isError":true,"content":[]}'
WRITE_PLAN='{"role":"assistant","content":[{"type":"toolCall","id":"w1","name":"write","arguments":{"path":"/ws/plans/p.md"}}]}'
WRITE_PLAN_OK='{"role":"toolResult","toolCallId":"w1","isError":false,"content":[]}'

@test "a new session starts on the standard tier" {
  run _decide '{"reason":"user","messages":[{"role":"user","content":"hi"}]}'
  [ "$status" -eq 0 ]
  [ "$output" = '{"tier":"standard"}' ]
}

@test "a planning skill loaded with /skill: moves the session to deep" {
  run _decide "{\"reason\":\"user\",\"messages\":[$SKILL_COMMAND]}"
  [ "$output" = '{"tier":"deep","state":{"phase":"planning"}}' ]
}

@test "a planning skill the model reads moves the session to deep" {
  run _decide "{\"reason\":\"continuation\",\"hasPrevious\":true,\"messages\":[$SKILL_READ]}"
  [ "$output" = '{"tier":"deep","state":{"phase":"planning"}}' ]
}

@test "any other skill leaves the session where it is" {
  run _decide "{\"reason\":\"user\",\"messages\":[$OTHER_SKILL]}"
  [ "$output" = '{"tier":"standard"}' ]
}

@test "writing the plan itself keeps the session planning" {
  run _decide "{\"reason\":\"continuation\",\"hasPrevious\":true,\"state\":{\"phase\":\"planning\"},\"planFiles\":[\"/ws/plans/p.md\"],\"messages\":[$SKILL_COMMAND,$WRITE_PLAN,$WRITE_PLAN_OK]}"
  [ "$output" = '{"tier":"deep","keep":"previous"}' ]
}

@test "the first edit outside the plans moves the session to building, on standard" {
  run _decide "{\"reason\":\"continuation\",\"hasPrevious\":true,\"state\":{\"phase\":\"planning\"},\"messages\":[$SKILL_COMMAND,$EDIT_CODE,$EDIT_CODE_OK]}"
  [ "$output" = '{"tier":"standard","state":{"phase":"building"}}' ]
}

@test "an edit that failed changed nothing, so the session stays planning" {
  run _decide "{\"reason\":\"continuation\",\"hasPrevious\":true,\"state\":{\"phase\":\"planning\"},\"messages\":[$SKILL_COMMAND,$EDIT_CODE,$EDIT_CODE_FAILED]}"
  [ "$output" = '{"tier":"deep","keep":"previous"}' ]
}

@test "a planning skill loaded after the build brings deep back" {
  # A second feature in one session, after a small fix, plans on deep again.
  run _decide "{\"reason\":\"user\",\"state\":{\"phase\":\"building\"},\"messages\":[$EDIT_CODE,$EDIT_CODE_OK,$SKILL_COMMAND]}"
  [ "$output" = '{"tier":"deep","state":{"phase":"planning"}}' ]
}

@test "an edit after the planning skill leaves the old load behind" {
  run _decide "{\"reason\":\"user\",\"state\":{\"phase\":\"building\"},\"messages\":[$SKILL_COMMAND,$EDIT_CODE,$EDIT_CODE_OK]}"
  [ "$output" = '{"tier":"standard"}' ]
}

@test "with both events compacted out of view the stored phase stands" {
  run _decide '{"reason":"user","state":{"phase":"planning"},"messages":[{"role":"user","content":"continue"}]}'
  [ "$output" = '{"tier":"deep"}' ]
}

@test "a tool follow-up stays on the model that answered" {
  run _decide '{"reason":"continuation","hasPrevious":true,"state":{"phase":"planning"}}'
  [ "$output" = '{"tier":"deep","keep":"previous"}' ]
}

@test "a retry stays on the model that failed" {
  run _decide '{"reason":"retry","hasFailed":true,"hasPrevious":true}'
  [ "$output" = '{"tier":"standard","keep":"failed"}' ]
}

@test "a user turn routes to the phase's tier, not to whatever answered last" {
  run _decide '{"reason":"user","hasPrevious":true,"state":{"phase":"planning"}}'
  [ "$output" = '{"tier":"deep"}' ]
}

@test "a request outside the agent loop goes to light and carries no state" {
  run _decide "{\"reason\":\"direct\",\"hasPrevious\":true,\"state\":{\"phase\":\"planning\"},\"messages\":[$EDIT_CODE,$EDIT_CODE_OK]}"
  [ "$output" = '{"tier":"light"}' ]
}

# ── The tiers ─────────────────────────────────────────────────────────────────

_parse_tiers() {
  node --input-type=module -e "
    const { parseTiers } = await import('$ROUTER/detect.ts');
    try { console.log(JSON.stringify(parseTiers(process.argv[1]))); }
    catch (error) { console.log('ERROR ' + error.message); process.exit(3); }
  " "$1"
}

@test "the tiers are read from config models' tiers" {
  run _parse_tiers '{"tiers":{"deep":{"provider":"anthropic","id":"claude-opus-5-5","level":"high"},"standard":{"provider":"anthropic","id":"claude-sonnet-5-5","level":null},"light":{"provider":"anthropic","id":"claude-haiku-5-5","level":null}}}'
  [ "$status" -eq 0 ]
  [[ "$output" == *'"deep":{"provider":"anthropic","id":"claude-opus-5-5","level":"high"}'* ]]
}

@test "a tier unset or naming no provider is refused, naming the key" {
  run _parse_tiers '{"tiers":{"deep":{"provider":"anthropic","id":"claude-opus-5-5","level":null},"standard":{"provider":null,"id":"claude-sonnet-5-5","level":null}}}'
  [ "$status" -eq 3 ]
  [[ "$output" == *"set models.standard, models.light in config.yml"* ]]
}

@test "the router's provider and id are the ones sync writes" {
  run node --input-type=module -e "
    const { ROUTER_PROVIDER, ROUTER_ID } = await import('$ROUTER/route.ts');
    console.log(ROUTER_PROVIDER + '/' + ROUTER_ID);
  "
  [ "$status" -eq 0 ]
  run python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT/ai/lib')
from agent.harness_models import ROUTER_PROVIDER, ROUTER_ID
print(f'{ROUTER_PROVIDER}/{ROUTER_ID}')
"
  [ "$output" = "workbench/auto" ]
}

# ── Plans and specs ──────────────────────────────────────────────────────────

# _plan_test CWD PATH... — planFileTest(CWD) on each PATH, one yes/no per line.
_plan_test() {
  node --input-type=module -e "
    const { planFileTest } = await import('$ROUTER/detect.ts');
    const [cwd, ...paths] = process.argv.slice(1);
    const test = planFileTest(cwd);
    for (const path of paths) console.log(test(path) ? 'yes' : 'no');
  " "$@"
}

@test "a plan or spec in the workspace is told from the code beside it" {
  local root container
  root="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  container="$root/container"
  git init -q --initial-branch=main "$root/seed"
  git -C "$root/seed" -c user.email=t@e -c user.name=T commit -q --allow-empty -m init
  mkdir -p "$container"
  git clone -q --bare "$root/seed" "$container/.git"
  git -C "$container" worktree add -q "$container/feature" -b feature

  run _plan_test "$container/feature" \
    "$container/workspace/plans/p.md" "$container/workspace/specs/new/s.md" \
    "src/app.py" "$container/feature/workspace/plans/p.md" "$container/workspace/plansish.md"
  [ "$status" -eq 0 ]
  [ "$output" = $'yes\nyes\nno\nno\nno' ]
}

@test "a session started from the container itself still tells a plan from code" {
  local root container
  root="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  container="$root/container"
  git init -q --initial-branch=main "$root/seed"
  git -C "$root/seed" -c user.email=t@e -c user.name=T commit -q --allow-empty -m init
  mkdir -p "$container"
  git clone -q --bare "$root/seed" "$container/.git"
  git -C "$container" worktree add -q "$container/feature" -b feature

  run _plan_test "$container" "workspace/specs/s.md" "feature/src/app.py"
  [ "$status" -eq 0 ]
  [ "$output" = $'yes\nno' ]
}

@test "outside a repository with a workspace nothing is a plan" {
  run _plan_test "$BATS_TEST_TMPDIR" "$BATS_TEST_TMPDIR/plans/p.md"
  [ "$status" -eq 0 ]
  [ "$output" = "no" ]
}

# ── The registered model, end to end ─────────────────────────────────────────

# _route REQUEST_JSON — loads index.ts with a stand-in `pi` and `ctx`, routes
# REQUEST_JSON, and prints the provider/id and level it went to. index.ts
# imports only types from the SDK, so node loads it; the tiers come from the
# real `config models` against the sandbox config.
_route() {
  node --input-type=module -e "
    const { default: register } = await import('$ROUTER/index.ts');
    let definition;
    register({ registerVirtualModel: (d) => { definition = d; } });
    const ctx = {
      cwd: process.argv[2],
      modelRegistry: { find: (provider, id) => provider === 'nowhere' ? undefined : { provider, id, contextWindow: id.endsWith('luna') ? 200000 : 1000000 } },
    };
    const request = JSON.parse(process.argv[1]);
    try {
      const route = definition.route({ thinkingLevel: 'medium', messages: [], ...request }, ctx);
      console.log(definition.provider + '/' + definition.id, route.model.provider + '/' + route.model.id, route.thinkingLevel);
    } catch (error) { console.log('ERROR ' + error.message); process.exit(3); }
  " "$1" "$BATS_TEST_TMPDIR"
}

_seed_tiers() {
  export WORKBENCH_CONFIG_DIR="$BATS_TEST_TMPDIR/config"
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf '%s\n' 'models:' "  deep: $1" "  standard: $2" "  light: $3" '  session: auto' \
    > "$WORKBENCH_CONFIG_DIR/config.yml"
}

@test "a user turn reaches the standard tier's model at its level" {
  _seed_tiers anthropic/claude-opus-5-5:high anthropic/claude-sonnet-5-5:low openai/gpt-6-luna
  run _route '{"reason":"user"}'
  [ "$status" -eq 0 ]
  [ "$output" = "workbench/auto anthropic/claude-sonnet-5-5 low" ]
}

@test "a tier with no level runs at the level the session selected" {
  _seed_tiers anthropic/claude-opus-5-5 anthropic/claude-sonnet-5-5 openai/gpt-6-luna
  run _route '{"reason":"direct"}'
  [ "$output" = "workbench/auto openai/gpt-6-luna medium" ]
}

@test "a follow-up keeps the model that answered, at its level" {
  _seed_tiers anthropic/claude-opus-5-5 anthropic/claude-sonnet-5-5 openai/gpt-6-luna
  run _route '{"reason":"continuation","previous":{"model":{"provider":"p","id":"answered"},"thinkingLevel":"high"}}'
  [ "$output" = "workbench/auto p/answered high" ]
}

@test "a summary goes to light only when light can hold the conversation" {
  _seed_tiers anthropic/claude-opus-5-5 anthropic/claude-sonnet-5-5 openai/gpt-6-luna
  run _route '{"reason":"direct","previous":{"model":{"provider":"anthropic","id":"claude-opus-5-5","contextWindow":1000000},"thinkingLevel":"high"}}'
  [ "$output" = "workbench/auto anthropic/claude-opus-5-5 high" ]
  run _route '{"reason":"direct","previous":{"model":{"provider":"anthropic","id":"claude-opus-5-5","contextWindow":100000}}}'
  [ "$output" = "workbench/auto openai/gpt-6-luna medium" ]
}

@test "the virtual model offers every level a tier may carry" {
  run node --input-type=module -e "
    const { default: register } = await import('$ROUTER/index.ts');
    let definition;
    register({ registerVirtualModel: (d) => { definition = d; } });
    console.log(definition.thinkingLevels.join(','));
  "
  [ "$output" = "off,minimal,low,medium,high,xhigh,max" ]
  run python3 -c "
import sys; sys.path.insert(0, '$REPO_ROOT/ai/lib')
from core.phases import Thinking
print(','.join(Thinking))
"
  [ "$output" = "off,minimal,low,medium,high,xhigh,max" ]
}

@test "a config that cannot be read ends the request with what config models said" {
  _seed_tiers anthropic/claude-opus-5-5 anthropic/claude-sonnet-5-5 openai/gpt-6-luna
  printf '%s\n' 'models:' '  session: auto' > "$WORKBENCH_CONFIG_DIR/config.yml"
  run _route '{"reason":"user"}'
  [ "$status" -eq 3 ]
  [[ "$output" == "ERROR workbench/auto could not read the tiers: models.session: auto routes between all three tiers"* ]]
}

@test "a tier pi does not list ends the request with the key to fix" {
  _seed_tiers anthropic/claude-opus-5-5 nowhere/model openai/gpt-6-luna
  run _route '{"reason":"user"}'
  [ "$status" -eq 3 ]
  [[ "$output" == *"models.standard is nowhere/model, which pi does not list"* ]]
}
