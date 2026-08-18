const RINGS = [
  { r: 2.6, c: 'var(--ow-amarillo)' },
  { r: 4.5, c: 'var(--ow-amarillo)' },
  { r: 6.4, c: 'var(--ow-amarillo)' },
  { r: 8.3, c: 'var(--ow-amarillo)' },
  { r: 10.2, c: 'var(--ow-rosa)' },
  { r: 12.1, c: 'var(--ow-rosa)' },
  { r: 14, c: 'var(--ow-rosa)' },
  { r: 15.9, c: 'var(--ow-anil)' },
  { r: 17.8, c: 'var(--ow-anil)' },
];

export function Rings({ size = 290 }: { size?: number }) {
  const strokeWidth = 180 / size;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      className="pointer-events-none absolute -right-16 -top-14"
    >
      {RINGS.map((ring) => (
        <circle
          key={ring.r}
          cx="16"
          cy="16"
          r={ring.r}
          stroke={ring.c}
          strokeWidth={strokeWidth}
        />
      ))}
    </svg>
  );
}
