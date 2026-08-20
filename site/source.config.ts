import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { remarkDocLinks } from './mdx/remark-doc-links.mjs';
import { remarkStripTitle } from './mdx/remark-strip-title.mjs';

// The .src.md files are compose-docs inputs, not pages — each one has a
// composed .md sibling that is the page. Serving both would publish every
// generated section twice, once with the include directive still in it.
export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    files: ['*.md', '!*.src.md'],
  },
});

export default defineConfig({
  mdxOptions: {
    remarkPlugins: (existing) => [remarkStripTitle, remarkDocLinks, ...existing],
  },
});
