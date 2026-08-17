import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { remarkDocLinks } from './mdx/remark-doc-links.mjs';
import { remarkStripTitle } from './mdx/remark-strip-title.mjs';

export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    files: ['*.md'],
  },
});

export default defineConfig({
  mdxOptions: {
    remarkPlugins: (existing) => [remarkStripTitle, remarkDocLinks, ...existing],
  },
});
