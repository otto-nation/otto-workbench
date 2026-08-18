const TIERS = [
  { title: 'Core', accent: 'var(--ow-amarillo)', body: 'Always synced, on every machine', items: 'bin · git · task · zsh' },
  { title: 'Optional', accent: 'var(--ow-rosa)', body: 'Opt in from the install menu', items: 'brew · docker · terminals · editors · ai · mise' },
];

export function HowItWorks() {
  return (
    <section className="px-6 pb-8">
      <p className="mb-4 font-[family-name:var(--font-mono)] text-[10px] tracking-[0.15em] text-[var(--ow-ink-muted)]">
        HOW IT WORKS
      </p>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {TIERS.map((tier) => (
          <div
            key={tier.title}
            className="rounded-lg border border-[var(--ow-hairline)] p-4"
            style={{ borderLeft: `3px solid ${tier.accent}` }}
          >
            <h2 className="text-sm font-bold tracking-tight">{tier.title}</h2>
            <p className="mt-1 text-xs text-[var(--ow-ink-muted)]">{tier.body}</p>
            <p className="mt-3 font-[family-name:var(--font-mono)] text-xs text-[var(--ow-ink-muted)]">
              {tier.items}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}
