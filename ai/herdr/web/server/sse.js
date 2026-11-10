/**
 * Server-sent events to every connected page. A `: ping` comment every heartbeatMs keeps proxies
 * and Bun's idle timeout from closing a quiet stream.
 *
 * Backpressure: enqueue is skipped (the client stays connected) when `desiredSize <= 0`. A stalled
 * reader therefore never grows the queue past the stream's high-water mark; later snapshots go out
 * once it consumes. Intermediate snapshots may be skipped — the board is last-write-wins. A closed
 * stream (`desiredSize == null`) drops the client and stops its heartbeat, same as a throwing enqueue.
 */
import { SECURITY_HEADERS } from './app.js';

export function createHub({
  heartbeatMs = 15000, every = setInterval, stopEvery = clearInterval, highWaterMark = 8,
} = {}) {
  const clients = new Set();
  const enc = new TextEncoder();
  const frame = (event, data) => enc.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  const drop = (entry) => {
    if (!entry || !clients.has(entry)) return;
    clients.delete(entry);
    if (entry.timer != null) stopEvery(entry.timer);
    entry.timer = undefined;
  };
  const push = (entry, chunk) => {
    const size = entry.controller.desiredSize;
    if (size == null) {
      drop(entry);
      return;
    }
    if (size <= 0) return;
    entry.controller.enqueue(chunk);
  };
  return {
    count: () => clients.size,
    subscribe(initial = []) {
      let entry;
      const stream = new ReadableStream({
        start(controller) {
          entry = { controller, timer: undefined };
          clients.add(entry);
          try {
            for (const [event, data] of initial) push(entry, frame(event, data));
          } catch {
            drop(entry);
            return;
          }
          entry.timer = every(() => {
            try { push(entry, enc.encode(': ping\n\n')); } catch { drop(entry); }
          }, heartbeatMs);
        },
        cancel() {
          drop(entry);
        },
      }, { highWaterMark });
      return new Response(stream, {
        headers: {
          ...SECURITY_HEADERS,
          'content-type': 'text/event-stream; charset=utf-8',
          'cache-control': 'no-store',
          'x-accel-buffering': 'no',
        },
      });
    },
    broadcast(event, data) {
      const f = frame(event, data);
      for (const entry of [...clients]) {
        try { push(entry, f); } catch { drop(entry); }
      }
    },
  };
}

// A whole snapshot only when the board changed; a small status event when only the clock moved.
export function changeFeed(hub) {
  let last = null;
  return (snap) => {
    const sig = JSON.stringify({ ...snap, updatedAt: 0, refreshing: false });
    if (sig !== last) {
      last = sig;
      hub.broadcast('board', snap);
    } else {
      hub.broadcast('status', { updatedAt: snap.updatedAt, refreshing: snap.refreshing });
    }
  };
}
