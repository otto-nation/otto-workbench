import Link from 'next/link';
import { Greca } from '@/components/greca';

export function Footer() {
  return (
    <footer className="flex items-center justify-between bg-[#1a1712] px-6 py-5 text-[#e8e3d8]">
      <span className="flex items-center gap-2">
        <Greca size={14} onDark />
        <span className="font-[family-name:var(--font-mono)] text-[10px] text-[#8a8073]">
          MIT · otto-nation
        </span>
      </span>
      <Link
        href="/docs/getting-started"
        className="rounded-md border border-[#3d3420] px-4 py-2 text-xs font-semibold"
      >
        Read the docs →
      </Link>
    </footer>
  );
}
