import './global.css';
import { RootProvider } from 'fumadocs-ui/provider/next';
import type { ReactNode } from 'react';

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="flex flex-col min-h-screen">
        {/* Static export has no search server; point the search dialog at the
            statically-exported index emitted by app/api/search/route.ts. */}
        {/* ceiling: `options.type` is @deprecated in fumadocs-ui 16.14.4; the
            newer `search={{ client: staticClient() }}` shape doesn't exist at
            this pinned version. Revisit on the next fumadocs-ui major. */}
        <RootProvider search={{ options: { type: 'static' } }}>{children}</RootProvider>
      </body>
    </html>
  );
}
