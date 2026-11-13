/**
 * Per-run model overrides for the PR surfaces. Pure: the picker holds the text,
 * this turns it into `pr --phase-model PHASE=MODEL` flags and a one-line summary.
 * `pr` owns validation (the phase exists, the model parses, a review phase is not
 * on a light model), so a bad value comes back as that run's own error.
 */

// The phases a review run uses.
export const REVIEW_PHASES = ['single', 'holistic', 'scout', 'group', 'synthesis', 'disprove'];

// A batch also runs the steps that fix and verify comments and CI, and rebase.
export const BATCH_PHASES = [
  ...REVIEW_PHASES,
  'comments_triage', 'comments_fix', 'comments_verify', 'ci_fix', 'ci_verify', 'rebase',
];

// The detail actions that run agents; posting and dismissing only talk to GitHub.
export const RUNS_MODELS = new Set(['review', 'rerun', 'recover']);

// [phase, model] for each phase in *order* with a non-blank value in *values*.
export function overrideEntries(values, order) {
  const out = [];
  for (const phase of order) {
    const model = String(values?.[phase] ?? '').trim();
    if (model) out.push([phase, model]);
  }
  return out;
}

export function phaseModelFlags(values, order) {
  return overrideEntries(values, order).flatMap(([phase, model]) => ['--phase-model', `${phase}=${model}`]);
}

// Argv for `pr`, flags first: --phase-model is global, so it goes before the subcommand.
export function withPhaseModels(argv, values, order) {
  const flags = phaseModelFlags(values, order);
  if (!flags.length || !Array.isArray(argv)) return argv;
  const [head, ...rest] = argv;
  return [head, ...flags, ...rest];
}

export function overrideSummary(values, order) {
  return overrideEntries(values, order).map(([phase, model]) => `${phase}=${model}`).join(' · ');
}
