import Link from 'next/link';
import { Greca } from '@/components/greca';

export function Footer() {
  return (
    <footer className="flex items-center justify-between bg-[var(--ow-block)] px-6 py-5 text-[var(--ow-block-ink)]">
      <span className="flex items-center gap-2">
        <Greca size={14} onDark />
        <span className="font-mono text-[10px] text-[var(--ow-block-ink-muted)]">
          MIT · otto-nation
        </span>
      </span>
      <Link
        href="/docs/getting-started"
        className="rounded-md border border-[var(--ow-block-hairline)] px-4 py-2 text-xs font-semibold"
      >
        Read the docs →
      </Link>
    </footer>
  );
}
