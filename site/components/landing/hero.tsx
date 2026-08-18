import Link from 'next/link';
import { Rings } from '@/components/rings';

export function Hero() {
  return (
    <section className="relative overflow-hidden px-6 pb-9 pt-11">
      <Rings />
      <div className="relative max-w-full sm:max-w-[62%]">
        <p className="mb-4 font-[family-name:var(--font-mono)] text-[10px] tracking-[0.16em] text-[var(--ow-ink-muted)]">
          ENVIRONMENT MANAGER
        </p>
        <h1 className="text-5xl font-extrabold leading-[0.94] tracking-[-0.022em]">
          One command.
          <br />
          Every machine.
        </h1>
        <p className="mt-4 max-w-[88%] leading-relaxed text-[var(--ow-ink-muted)]">
          Shell config, git settings, brew packages, editor preferences, and AI coding tools —
          managed through a component framework that keeps everything reproducible and in sync.
        </p>
        <div className="mt-5 flex gap-2">
          <Link
            href="/docs/getting-started"
            className="rounded-md bg-[var(--ow-ink)] px-4 py-2 text-sm font-semibold text-[var(--ow-canvas)]"
          >
            Get started
          </Link>
          <a
            href="https://github.com/otto-nation/otto-workbench"
            className="rounded-md border border-[var(--ow-hairline)] px-4 py-2 text-sm font-semibold"
          >
            GitHub
          </a>
        </div>
      </div>
    </section>
  );
}
