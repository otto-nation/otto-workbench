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

const SIZE = 290;
const STROKE_WIDTH = 180 / SIZE;

export function Rings() {
  return (
    <svg
      width={SIZE}
      height={SIZE}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      className="pointer-events-none absolute -right-16 -top-14 h-20 w-20 sm:h-[290px] sm:w-[290px]"
    >
      {RINGS.map((ring) => (
        <circle
          key={ring.r}
          cx="16"
          cy="16"
          r={ring.r}
          stroke={ring.c}
          strokeWidth={STROKE_WIDTH}
        />
      ))}
    </svg>
  );
}
