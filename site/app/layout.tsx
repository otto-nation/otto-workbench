import './global.css';
import { RootProvider } from 'fumadocs-ui/provider/next';
import { League_Spartan } from 'next/font/google';
import localFont from 'next/font/local';
import type { ReactNode } from 'react';

const display = League_Spartan({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-display',
});

const mono = localFont({
  src: './fonts/LeagueMonoVariable.woff2',
  weight: '100 900',
  display: 'swap',
  variable: '--font-mono',
});

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html
      lang="en"
      className={`${display.variable} ${mono.variable}`}
      suppressHydrationWarning
    >
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
