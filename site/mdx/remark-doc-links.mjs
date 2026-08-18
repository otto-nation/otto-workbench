import { visit } from 'unist-util-visit';

const REPO = 'https://github.com/otto-nation/otto-workbench';
const DOC_PAGE = /^([a-z0-9-]+)\.md(#.*)?$/;

/**
 * Rewrite one link target from repo-relative to site-absolute.
 *
 * Docs are flat in `docs/`, so a sibling `.md` maps straight to a route. Anything
 * reached with `../` lives outside the collection and has no page on the site, so it
 * points back at GitHub instead of 404ing.
 *
 * @param {string} url
 * @returns {string}
 */
export function rewrite(url) {
  if (!url || url.startsWith('#') || url.startsWith('/') || /^[a-z][a-z0-9+.-]*:/i.test(url)) {
    return url;
  }

  const target = url.startsWith('./') ? url.slice(2) : url;

  const page = DOC_PAGE.exec(target);
  if (page) return `/docs/${page[1]}${page[2] ?? ''}`;

  // `docs/` is one level below the repo root, so a single `../` lands at the root.
  // Deeper traversals are not a form this repo uses; leave them for the link test to catch.
  if (target.startsWith('../') && !target.slice(3).startsWith('../')) {
    const path = target.slice(3);
    const kind = path.split('#')[0].endsWith('/') ? 'tree' : 'blob';
    return `${REPO}/${kind}/main/${path}`;
  }

  return url;
}

export function remarkDocLinks() {
  return (tree) => {
    visit(tree, 'link', (node) => {
      node.url = rewrite(node.url);
    });
  };
}
