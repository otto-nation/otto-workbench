import Link from 'next/link';
import { Greca } from '@/components/greca';

export function Nav() {
  return (
    <nav className="flex items-center justify-between border-b border-[var(--ow-hairline)] px-6 py-4">
      <span className="flex items-center gap-2 text-sm font-bold tracking-tight">
        <Greca size={17} />
        otto-workbench
      </span>
      <span className="flex items-center gap-4 font-[family-name:var(--font-mono)] text-xs text-[var(--ow-ink-muted)]">
        <Link href="/docs/getting-started">docs</Link>
        <a href="https://github.com/otto-nation/otto-workbench">github</a>
      </span>
    </nav>
  );
}
