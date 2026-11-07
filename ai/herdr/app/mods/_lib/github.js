/**
 * Pure argv builders and JSON mappers for gh GitHub PR fetches.
 * Callers run `exec` with the argv; this module imports nothing.
 *
 * REST pull (mapPulls):
 *   { number, title, author, branch, baseBranch, draft, url, headSha,
 *     labels: string[], createdAt, updatedAt }
 *
 * GraphQL enrichment (mapGraphql):
 *   { isDraft, reviewDecision, mergeable, additions, deletions, changedFiles,
 *     ciStatus, inMergeQueue, latestReviews: [{ login, state }],
 *     requestedReviewers: [login], state, mergedAt }
 */

export function userArgv() {
  return ['api', '/user', '--jq', '.login'];
}

export function pullsArgv(repo) {
  // gh switches to POST when -f is used without --method GET
  return [
    'api',
    `/repos/${repo}/pulls`,
    '--paginate',
    '--slurp',
    '--method',
    'GET',
    '-f',
    'state=open',
    '-f',
    'per_page=100',
  ];
}

function intNums(numbers) {
  return (numbers ?? []).filter((n) => Number.isInteger(n));
}

// Precondition: repo is "owner/name" (as remoteSlug returns); no-slash input yields an empty owner.
function ownerName(repo) {
  const i = String(repo).indexOf('/');
  if (i <= 0) return { owner: '', name: String(repo) };
  return { owner: String(repo).slice(0, i), name: String(repo).slice(i + 1) };
}

const CHEAP_FIELDS = [
  'number',
  'isDraft',
  'reviewDecision',
  'mergeable',
  'additions',
  'deletions',
  'changedFiles',
  'author { login }',
  'mergeQueueEntry { id }',
  'commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }',
  'latestReviews(first: 20) { nodes { author { login } state } }',
  'reviewRequests(first: 10) { nodes { requestedReviewer { __typename ... on User { login } } } }',
].join(' ');

function aliasedPrs(numbers, extra) {
  const fields = extra ? `${extra} ${CHEAP_FIELDS}` : CHEAP_FIELDS;
  return numbers
    .map((num, i) => `pr${i}: pullRequest(number: ${num}) { ${fields} }`)
    .join(' ');
}

function graphqlQuery(repo, numbers, extra) {
  const { owner, name } = ownerName(repo);
  const inner = aliasedPrs(intNums(numbers), extra);
  return `query { repository(owner: "${owner}", name: "${name}") { ${inner} } }`;
}

export function graphqlArgv(repo, numbers) {
  return ['api', 'graphql', '-f', `query=${graphqlQuery(repo, numbers)}`];
}

export function landedArgv(repo, numbers) {
  return [
    'api',
    'graphql',
    '-f',
    `query=${graphqlQuery(repo, numbers, 'state merged mergedAt')}`,
  ];
}

export function batches(numbers, size = 20) {
  const src = numbers ?? [];
  if (src.length === 0) return [];
  const out = [];
  for (let i = 0; i < src.length; i += size) {
    out.push(src.slice(i, i + size));
  }
  return out;
}

function flattenPullPages(json) {
  if (!Array.isArray(json)) return [];
  if (json.length === 0) return [];
  if (Array.isArray(json[0])) return json.flat();
  return json;
}

export function mapPulls(json) {
  return flattenPullPages(json).map((pr) => ({
    number: pr.number,
    title: pr.title,
    author: pr.user?.login ?? '',
    branch: pr.head?.ref ?? '',
    baseBranch: pr.base?.ref ?? '',
    draft: Boolean(pr.draft),
    url: pr.html_url ?? '',
    headSha: pr.head?.sha ?? '',
    labels: (pr.labels ?? []).map((l) => l.name),
    createdAt: pr.created_at ?? null,
    updatedAt: pr.updated_at ?? null,
  }));
}

function mergeableOf(value) {
  if (value === 'MERGEABLE' || value === 'CONFLICTING') return value;
  return 'UNKNOWN';
}

function ciStatusOf(pr) {
  // ceiling: rollup only; add ui-code's check-runs fallback if a repo reports no rollup
  if (pr.isDraft) return 'none';
  const state = pr.commits?.nodes?.[0]?.commit?.statusCheckRollup?.state;
  if (state === 'FAILURE' || state === 'ERROR') return 'failing';
  if (state === 'PENDING' || state === 'EXPECTED') return 'pending';
  if (state === 'SUCCESS') return 'passing';
  return 'none';
}

function requestedLogins(pr) {
  const logins = [];
  for (const n of pr.reviewRequests?.nodes ?? []) {
    const r = n?.requestedReviewer;
    if (!r || r.__typename === 'Team') continue;
    if (r.__typename && r.__typename !== 'User') continue;
    if (r.login) logins.push(r.login);
  }
  return logins;
}

function latestReviewsOf(pr) {
  const out = [];
  for (const n of pr.latestReviews?.nodes ?? []) {
    const login = n?.author?.login;
    if (login) out.push({ login, state: n.state });
  }
  return out;
}

function mapOne(pr) {
  return {
    isDraft: Boolean(pr.isDraft),
    reviewDecision: pr.reviewDecision ?? null,
    mergeable: mergeableOf(pr.mergeable),
    additions: pr.additions ?? 0,
    deletions: pr.deletions ?? 0,
    changedFiles: pr.changedFiles ?? 0,
    ciStatus: ciStatusOf(pr),
    inMergeQueue: !!pr.mergeQueueEntry,
    latestReviews: latestReviewsOf(pr),
    requestedReviewers: requestedLogins(pr),
    state: pr.state ?? null,
    mergedAt: pr.mergedAt ?? null,
  };
}

export function mapGraphql(json, numbers) {
  if (json === null || typeof json !== 'object' || Array.isArray(json)) return {};
  const repo = json.data?.repository;
  if (!repo || typeof repo !== 'object') return {};
  const out = {};
  const nums = numbers ?? [];
  for (let i = 0; i < nums.length; i++) {
    const pr = repo[`pr${i}`];
    if (!pr || typeof pr !== 'object') continue;
    out[nums[i]] = mapOne(pr);
  }
  return out;
}
