'use client';

import { useState } from 'react';

const COMMAND = 'brew install otto-nation/tap/otto-workbench';

export function Install() {
  const [copied, setCopied] = useState(false);

  return (
    <section className="px-6 pb-8">
      <div className="overflow-hidden rounded-lg bg-[var(--ow-block)]">
        <div className="flex items-center justify-between border-b border-[var(--ow-block-hairline)] px-3 py-2">
          <span className="font-mono text-[9px] text-[var(--ow-block-ink-muted)]">zsh</span>
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
            className="font-mono text-[9px] text-[var(--ow-block-ink-muted)]"
          >
            {copied ? 'copied' : 'copy'}
          </button>
        </div>
        <pre className="overflow-x-auto px-4 py-4 font-mono text-xs leading-7 text-[var(--ow-block-ink)]">
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
