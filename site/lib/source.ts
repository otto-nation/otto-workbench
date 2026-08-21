import { loader } from 'fumadocs-core/source';
// fumadocs-core 16.14.4 has no `fumadocs-core/server` entry point or `PageTree`
// namespace; the page-tree node types are named exports of `fumadocs-core/page-tree`.
import type { Node as PageTreeNode, Root as PageTreeRoot } from 'fumadocs-core/page-tree';
// fumadocs-mdx 15.2.3 emits `.source/server.ts` (and browser.ts, dynamic.ts) — there is
// no `.source/index.ts` re-export, so the server-side collection is imported directly.
import { docs } from '@/.source/server';

export const source = loader({
  baseUrl: '/docs',
  source: docs.toFumadocsSource(),
});

export const SIDEBAR_ORDER = [
  'getting-started',
  'architecture',
  'execution-flow',
  'components',
  'registries',
  'libraries',
  'tools',
  'ai-automation',
  'ai-libraries',
  'user-overrides',
  'troubleshooting',
] as const;

function rank(node: PageTreeNode): number {
  const slug = 'url' in node ? (node.url.split('/').pop() ?? '') : '';
  const index = SIDEBAR_ORDER.indexOf(slug as (typeof SIDEBAR_ORDER)[number]);
  return index === -1 ? SIDEBAR_ORDER.length : index;
}

export function sortTree(tree: PageTreeRoot): PageTreeRoot {
  return { ...tree, children: [...tree.children].sort((a, b) => rank(a) - rank(b)) };
}
