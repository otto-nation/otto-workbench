/**
 * Pure argv builders for gh file-list and patch fetches.
 * Callers run `exec` with the argv; this module imports nothing.
 */

export const FILES_PER_PAGE = 100;

// The file list without patches: a 1,000-file PR's patches pass herdr's exec output cap, so a
// file's patch is fetched when it is opened (pagePatchesArgv / patchArgv). filesArgv paginates;
// filesPageArgv is one page (incremental listing). One JSON array per page.
const FILES_JQ =
  '[.[] | {filename, previous_filename, status, additions, deletions, has_patch: (.patch != null)}]';

// The page size rides in the URL: `-f` would make gh POST.
export function filesArgv(repo, number) {
  return ['api', `/repos/${repo}/pulls/${number}/files?per_page=${FILES_PER_PAGE}`, '--paginate', '--jq', FILES_JQ];
}

export function filesPageArgv(repo, number, page) {
  return [
    'api',
    `/repos/${repo}/pulls/${number}/files?per_page=${FILES_PER_PAGE}&page=${page}`,
    '--jq',
    FILES_JQ,
  ];
}

// One file's patch: the API has no per-file endpoint, so read the page the file is on and keep
// only its patch. JSON.stringify writes a valid jq string literal. Prints nothing when there is none.
export function patchArgv(repo, number, page, path) {
  return [
    'api', `/repos/${repo}/pulls/${number}/files?per_page=${FILES_PER_PAGE}&page=${page}`,
    '--jq', `.[] | select(.filename == ${JSON.stringify(path)}) | .patch // empty`,
  ];
}

// Every file's patch on one page, as `{filename: patch-or-null}`, so a second open on that page
// does not download it again.
const PAGE_PATCHES_JQ = 'map({(.filename): .patch}) | add';

export function pagePatchesArgv(repo, number, page) {
  return [
    'api',
    `/repos/${repo}/pulls/${number}/files?per_page=${FILES_PER_PAGE}&page=${page}`,
    '--jq',
    PAGE_PATCHES_JQ,
  ];
}
