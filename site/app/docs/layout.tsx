import { DocsLayout } from 'fumadocs-ui/layouts/docs';
import type { ReactNode } from 'react';
import { Greca } from '@otto-nation/brand';
import { source, sortTree } from '@/lib/source';

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <DocsLayout
      tree={sortTree(source.getPageTree())}
      nav={{
        title: (
          <span className="flex items-center gap-2 font-bold tracking-tight">
            <Greca size={15} />
            otto-workbench
          </span>
        ),
      }}
    >
      {children}
    </DocsLayout>
  );
}
