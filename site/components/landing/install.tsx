'use client';

import { useState } from 'react';

const COMMAND = 'brew install otto-nation/tap/otto-workbench';

export function Install() {
  const [copied, setCopied] = useState(false);

  return (
    <section className="px-6 pb-8">
      <div className="overflow-hidden rounded-lg bg-[#1a1712]">
        <div className="flex items-center justify-between border-b border-[#302a1e] px-3 py-2">
          <span className="font-[family-name:var(--font-mono)] text-[9px] text-[#6b6357]">zsh</span>
          <button
            type="button"
            aria-live="polite"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(COMMAND);
                setCopied(true);
                setTimeout(() => setCopied(false), 1500);
              } catch (error) {
                console.error('Failed to copy install command', error);
              }
            }}
            className="font-[family-name:var(--font-mono)] text-[9px] text-[#8a8073]"
          >
            {copied ? 'copied' : 'copy'}
          </button>
        </div>
        <pre className="overflow-x-auto px-4 py-4 font-[family-name:var(--font-mono)] text-xs leading-7 text-[#e8e3d8]">
          <span className="text-[var(--ow-amarillo)]">$ </span>
          {COMMAND}
          {'\n'}
          <span className="text-[var(--ow-amarillo)]">$ </span>otto-workbench install{'\n'}
          <span className="text-[var(--ow-amarillo)]">$ </span>exec zsh
        </pre>
      </div>
    </section>
  );
}
