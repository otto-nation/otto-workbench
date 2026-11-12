// Same-origin JSON calls and the event stream. A 401 means the session lapsed: reload to sign in.
async function checked(res) {
  if (res.status === 401) { location.reload(); throw new Error('signed out'); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(body.reason ?? body.error ?? `HTTP ${res.status}`), { status: res.status, body });
  return body;
}

export const getJson = (path) => fetch(path, { credentials: 'same-origin' }).then(checked);

export const postJson = (path, body) => fetch(path, {
  method: 'POST', credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body ?? {}),
}).then(checked);

export const q = (params) => new URLSearchParams(params).toString();

// The view is the tab the page shows; the server reads owed only while some page shows Mine.
export function events(handlers, { view = 'team' } = {}) {
  const es = new EventSource('/api/events?' + q({ view }));
  // EventSource retries on its own; a lapsed session shows as repeated errors, so ask once
  // per page load and again only after a successful message (reconnect storms otherwise).
  let probed = false;
  for (const [name, fn] of Object.entries(handlers)) {
    es.addEventListener(name, (e) => {
      let data;
      try { data = JSON.parse(e.data); } catch { return; }
      probed = false;
      fn(data);
    });
  }
  es.addEventListener('error', () => {
    if (probed) return;
    probed = true;
    getJson('/api/board').catch(() => {});
  });
  return es;
}
