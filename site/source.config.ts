import { defineConfig, defineDocs } from 'fumadocs-mdx/config';
import { pageSchema } from 'fumadocs-core/source/schema';

export const docs = defineDocs({
  dir: 'content/docs',
  docs: {
    files: ['*.md'],
    // The default pageSchema requires `title`, but none of the ten docs carry
    // frontmatter yet (Task 2 adds it). Relaxing it to optional lets this task's
    // build succeed today; fumadocs-mdx falls back to the first heading as the
    // title in its absence. An explicit `title` from Task 2 still validates fine.
    schema: pageSchema.extend({ title: pageSchema.shape.title.optional() }),
  },
});

export default defineConfig();
