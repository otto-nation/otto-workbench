// A stand-in for the parts of pi's ExtensionAPI and ExtensionContext that
// ai/pi/extensions/session-brief/wire.ts uses. appendEntry and setSessionName
// push onto `entries`, which getBranch() returns, as a real session does.
// Timers are recorded, never scheduled: a test fires one by calling its fn.
export function fakePi({ entries = [], name, mode = 'tui', file = '/s/x_s1.jsonl', reply, facts } = {}) {
  const calls = { handlers: {}, execs: [], completes: [], timers: [] };
  const state = { name };
  const control = {
    reply: reply ?? {
      stopReason: 'stop',
      content: [{ type: 'text', text: '{"title": "Fix login", "tldr": "Fixing the login race."}' }],
    },
    facts: facts ?? '{"worktrees": ["/w/a"], "prs": []}',
    factsCode: 0,
  };
  const haiku = { provider: 'anthropic', id: 'claude-haiku-4-5' };
  const pi = {
    on(event, fn) { calls.handlers[event] = fn; },
    appendEntry(customType, data) { entries.push({ type: 'custom', customType, data }); },
    setSessionName(n) { state.name = n; entries.push({ type: 'session_info', name: n }); },
    getSessionName() { return state.name; },
    async exec(command, args, options) {
      calls.execs.push({ command, args, options });
      if (args[1] === 'facts') return { stdout: control.facts, stderr: '', code: control.factsCode, killed: false };
      return { stdout: '', stderr: '', code: 0, killed: false };
    },
  };
  const ctx = {
    mode,
    cwd: '/w/a',
    model: { provider: 'anthropic', id: 'claude-opus' },
    modelRegistry: {
      find: (p, i) => (p === haiku.provider && i === haiku.id ? haiku : undefined),
      getAvailable: () => [haiku],
      async complete(model, context, options) {
        calls.completes.push({ model, context, options });
        return control.reply;
      },
    },
    sessionManager: { getBranch: () => entries, getEntries: () => entries, getSessionFile: () => file },
  };
  const deps = {
    env: {},
    pid: 4242,
    now: () => new Date('2026-11-17T12:00:00Z'),
    setTimer: (fn, ms) => { calls.timers.push({ fn, ms, cleared: false }); return calls.timers.length - 1; },
    clearTimer: (id) => { if (calls.timers[id]) calls.timers[id].cleared = true; },
    tiersText: () => JSON.stringify({ tiers: { light: { provider: null, id: 'claude-haiku-4-5', level: null } } }),
  };
  return { pi, ctx, deps, calls, control, entries, state };
}

/** The entries of one customType, in order. */
export const ofType = (entries, customType) => entries.filter((e) => e.customType === customType);

/** One user turn and the assistant's answer. */
export const turn = () => [
  { type: 'message', message: { role: 'user', content: 'fix the login race' } },
  { type: 'message', message: { role: 'assistant', content: [{ type: 'text', text: 'done' }] } },
];
