import { fileURLToPath } from 'node:url';
import { createMDX } from 'fumadocs-mdx/next';

/** @type {import('next').NextConfig} */
const config = {
  output: 'export',
  basePath: '/otto-workbench',
  images: { unoptimized: true },
  reactStrictMode: true,
  // `content/docs` is a symlink to `../../docs` (outside `site/`). Without an explicit
  // root, Turbopack treats `site/` as the filesystem boundary and refuses to follow a
  // symlink that resolves outside it ("Symlink ... points out of the filesystem root").
  // Pointing root at the repo root, which contains both `site/` and `docs/`, fixes this.
  turbopack: { root: fileURLToPath(new URL('..', import.meta.url)) },
};

export default createMDX()(config);
