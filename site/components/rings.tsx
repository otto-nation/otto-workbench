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

// Intrinsic size, and the desktop size the `sm:` classes below restate. Only a
// fallback: the classes win wherever the stylesheet loads.
const SIZE = 290;

// In CSS pixels, not viewBox units. A plain stroke-width is expressed in the
// 32-unit viewBox and so scales with the rendered box — the same value lands at
// 5.6px on the 290px desktop render and 1.6px on the 80px mobile one, which is
// why the rings went wispy below `sm`. `vector-effect: non-scaling-stroke`
// takes the width in viewport pixels instead, so one constant holds the line
// weight steady across both breakpoints.
const STROKE_PX = 2.4;

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
          strokeWidth={STROKE_PX}
          vectorEffect="non-scaling-stroke"
        />
      ))}
    </svg>
  );
}
