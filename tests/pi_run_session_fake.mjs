// A stand-in for the parts of pi's ExtensionAPI and ExtensionContext that
// ai/pi/extensions/run-session/wire.ts uses. Entries it appends are what
// ctx.sessionManager.getBranch() returns, as in a real session, so a second
// wire() handed the same `entries` is the same session reopened.
export function fakePi({ sessionId = "", entries = [], report = '{"sessions": []}' } = {}) {
  const calls = { handlers: {}, tools: [], renderers: {}, messages: [], execs: [] };
  const control = {
    report,
    reportCode: 0,
    noteCode: 0,
    noteStdout: '{"kind": "decision"}',
    noteStderr: "",
  };
  const pi = {
    on(name, fn) {
      calls.handlers[name] = fn;
    },
    registerTool(tool) {
      calls.tools.push(tool);
    },
    registerEntryRenderer(customType, fn) {
      calls.renderers[customType] = fn;
    },
    appendEntry(customType, data) {
      entries.push({ type: "custom", customType, data });
    },
    sendMessage(message, options) {
      calls.messages.push({ message, options });
    },
    async exec(command, args, options) {
      calls.execs.push({ command, args, options });
      if (args[1] === "note") {
        return { stdout: control.noteStdout, stderr: control.noteStderr, code: control.noteCode, killed: false, truncated: false };
      }
      const stderr = control.reportCode ? "pr batch: no run x" : "";
      return { stdout: control.report, stderr, code: control.reportCode, killed: false, truncated: false };
    },
  };
  const ctx = {
    cwd: "/checkout",
    hasUI: false,
    ui: { notify() {} },
    sessionManager: { getBranch: () => entries, getSessionId: () => sessionId },
  };
  return { pi, ctx, calls, control, entries };
}

/** The entries of one customType, in order. */
export function ofType(entries, customType) {
  return entries.filter((entry) => entry.customType === customType);
}
