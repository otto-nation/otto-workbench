import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { remarkDocLinks } from './mdx/remark-doc-links.mjs';
import { remarkStripTitle } from './mdx/remark-strip-title.mjs';

// The .src.md files are compose-docs inputs, not pages — each one has a
// composed .md sibling that is the page. Serving both would publish every
// generated section twice, once with the include directive still in it.
//
// One extglob rather than `['*.md', '!*.src.md']`, because fumadocs-mdx reads
// this list two ways. Page discovery globs it, where a leading `!` does
// subtract; the dev-server watcher hands one filename at a time to picomatch,
// where an array is a plain OR and `*.md` has already matched `x.src.md`
// before the negation is consulted. The extglob excludes under both.
export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    files: ['!(*.src).md'],
  },
});

export default defineConfig({
  mdxOptions: {
    remarkPlugins: (existing) => [remarkStripTitle, remarkDocLinks, ...existing],
  },
});
