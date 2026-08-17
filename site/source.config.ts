import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { pageSchema } from 'fumadocs-core/source/schema';

export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    files: ['*.md'],
    // Temporary scaffolding: the default pageSchema requires `title`, but none of
    // the ten docs carry frontmatter yet. The task that adds `title`/`description`
    // frontmatter to all ten docs deletes this override, restoring the default
    // pageSchema — do not carry it past that task.
    schema: pageSchema.extend({ title: pageSchema.shape.title.optional() }),
  },
});

export default defineConfig();
