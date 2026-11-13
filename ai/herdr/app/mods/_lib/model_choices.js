/**
 * The models a phase can be set to, as a picker list. Pure: `pr models --json`
 * is the document, this turns it into argv, a schema check, and the sections a
 * phase's popover draws. The value passed to `--phase-model` is a row's `ref`
 * verbatim. Review-floor rows are disabled only when the phase has
 * `review_floor`; the reason is a caption, not a hidden row.
 */

export const CHOICES_SCHEMA = 1;

export const SECTION_CONFIGURED = 'Configured';
export const SECTION_TIERS = 'Tiers';
export const SECTION_ENABLED = 'Enabled in Pi';

export const FLOOR_REASON = 'Review phases refuse light and Haiku models';

function fold(s) {
  return String(s ?? '').toLowerCase();
}

function isObject(v) {
  return v != null && typeof v === 'object' && !Array.isArray(v);
}

// Argv for `pr models --json`. `repoDir` is the checkout so project tiers apply.
export function choicesArgv(repoDir) {
  const args = ['pr', 'models', '--json'];
  const dir = String(repoDir ?? '').trim();
  if (dir) args.push('--repo-dir', dir);
  return args;
}

export function parseChoices(doc) {
  if (!isObject(doc)) {
    return { ok: false, error: { kind: 'bad-json', message: 'not an object' } };
  }
  if (doc.schema !== CHOICES_SCHEMA) {
    return { ok: false, error: { kind: 'outdated', message: 'unknown schema' } };
  }
  if (!isObject(doc.phases) || !isObject(doc.tiers) || !Array.isArray(doc.enabled)) {
    return { ok: false, error: { kind: 'bad-json', message: 'missing phases, tiers or enabled' } };
  }
  if (!isObject(doc.catalogue)) {
    return { ok: false, error: { kind: 'bad-json', message: 'missing catalogue' } };
  }
  return { ok: true, doc };
}

export function defaultLabel(doc, phase) {
  const ref = doc?.phases?.[phase]?.default?.ref;
  if (ref) return `${SECTION_CONFIGURED} · ${ref}`;
  return SECTION_CONFIGURED;
}

function matches(query, ...parts) {
  const q = fold(query).trim();
  if (!q) return true;
  return parts.some((p) => fold(p).includes(q));
}

function reviewOkLookup(doc, ref) {
  for (const info of Object.values(doc.tiers ?? {})) {
    if (info?.ref === ref && typeof info.review_ok === 'boolean') return info.review_ok;
  }
  for (const provider of doc.catalogue?.providers ?? []) {
    for (const model of provider.models ?? []) {
      if (model?.ref === ref && typeof model.review_ok === 'boolean') return model.review_ok;
    }
  }
  return true;
}

function optionRow({ ref, label, reviewOk, floor, current }) {
  const disabled = Boolean(floor) && reviewOk === false;
  return {
    ref,
    label,
    selected: String(current ?? '') === String(ref ?? ''),
    disabled,
    reason: disabled ? FLOOR_REASON : null,
  };
}

function pushSection(sections, label, rows) {
  if (!rows.length) return;
  sections.push({ label, rows });
}

// Sections Configured / Tiers / Enabled in Pi / by provider. `current` is the
// value already chosen for the phase (blank means configured).
export function phaseOptions(doc, phase, query, current = '') {
  const parsed = parseChoices(doc);
  if (!parsed.ok) return { ok: false, error: parsed.error, sections: [] };
  const d = parsed.doc;
  const floor = Boolean(d.phases?.[phase]?.review_floor);
  const q = query;
  const cur = current;
  const sections = [];

  const configured = [];
  const configuredRow = optionRow({
    ref: '',
    label: defaultLabel(d, phase),
    reviewOk: true,
    floor: false,
    current: cur,
  });
  if (matches(q, configuredRow.label, SECTION_CONFIGURED)) configured.push(configuredRow);
  pushSection(sections, SECTION_CONFIGURED, configured);

  const tiers = [];
  for (const [tier, info] of Object.entries(d.tiers ?? {})) {
    const ref = info?.ref;
    if (ref == null || ref === '') continue;
    if (!matches(q, tier, ref)) continue;
    tiers.push(optionRow({
      ref,
      label: info.ref ? `${tier} · ${ref}` : tier,
      reviewOk: info.review_ok !== false,
      floor,
      current: cur,
    }));
  }
  pushSection(sections, SECTION_TIERS, tiers);

  const enabled = [];
  for (const ref of d.enabled ?? []) {
    if (!matches(q, ref)) continue;
    enabled.push(optionRow({
      ref,
      label: ref,
      reviewOk: reviewOkLookup(d, ref),
      floor,
      current: cur,
    }));
  }
  pushSection(sections, SECTION_ENABLED, enabled);

  for (const provider of d.catalogue?.providers ?? []) {
    const name = provider?.provider;
    if (!name) continue;
    const rows = [];
    for (const model of provider.models ?? []) {
      const ref = model?.ref;
      if (ref == null) continue;
      const label = model.id ?? ref;
      if (!matches(q, label, ref, name, model.id)) continue;
      rows.push(optionRow({
        ref,
        label,
        reviewOk: model.review_ok !== false,
        floor,
        current: cur,
      }));
    }
    pushSection(sections, name, rows);
  }

  return { ok: true, sections };
}

// True iff phaseOptions would mark this ref's row disabled for the phase.
// No doc / unknown ref / blank Configured: fail-open, nothing blocked; pr still validates.
export function refBlocked(doc, phase, ref) {
  const r = String(ref ?? '').trim();
  if (!r) return false;
  const opts = phaseOptions(doc, phase, '', r);
  if (!opts.ok) return false;
  return opts.sections.some((s) => s.rows.some((row) => row.ref === r && row.disabled));
}
