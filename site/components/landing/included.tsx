import Link from 'next/link';

const ITEMS = [
  { title: 'Scripts', href: '/docs/tools#scripts', body: 'Workbench utilities for environment management, validation, and code generation' },
  { title: 'Shell', href: '/docs/architecture#shell-zsh', body: 'ZSH with modular config layers, Starship prompt, and lazy-loaded plugins' },
  { title: 'Git', href: '/docs/architecture#git', body: 'Two-layer gitconfig, global hooks, and conventional commit conventions' },
  { title: 'Tools', href: '/docs/tools#installed-tools', body: 'CLI tools managed via Homebrew, organized by domain' },
  { title: 'AI', href: '/docs/ai-automation', body: 'Claude Code integration with skills, agents, guidelines, and git automation' },
  { title: 'Task automation', href: '/docs/ai-automation#task-automation', body: 'Global Taskfile for AI-powered commits, PRs, and reviews' },
];

export function Included() {
  return (
    <section className="px-6 py-7">
      <p className="mb-5 font-[family-name:var(--font-mono)] text-[10px] tracking-[0.15em] text-[var(--ow-ink-muted)]">
        WHAT&apos;S INCLUDED
      </p>
      <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {ITEMS.map((item) => (
          <Link
            key={item.title}
            href={item.href}
            className="-m-4 block rounded-lg border border-transparent p-4 transition-colors hover:border-[var(--ow-hairline)] focus-visible:border-[var(--ow-hairline)]"
          >
            <h2 className="text-sm font-bold tracking-tight">{item.title}</h2>
            <p className="mt-1 text-xs leading-relaxed text-[var(--ow-ink-muted)]">{item.body}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
