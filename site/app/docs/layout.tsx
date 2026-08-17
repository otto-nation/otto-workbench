import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import type { ReactNode } from 'react';
import { source, sortTree } from '@/lib/source';

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <DocsLayout tree={sortTree(source.getPageTree())} nav={{ title: 'otto-workbench' }}>
      {children}
    </DocsLayout>
  );
}
