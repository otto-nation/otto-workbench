import { useId } from 'react';

export function Greca({ size = 18, onDark = false }: { size?: number; onDark?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path
        d="M4 28V18h8v-6h8V6h8"
        stroke={onDark ? 'var(--ow-amarillo)' : 'var(--ow-barro)'}
        strokeWidth="3"
      />
      <path d="M12 28v-4h8v-6h8" stroke="var(--ow-rosa)" strokeWidth="3" />
    </svg>
  );
}

// Rendered as an inline SVG with a <pattern> fill rather than a CSS
// background-image data-URI: a data-URI cannot read CSS custom properties,
// so its stroke would be a hardcoded hex and stay light in dark mode. The
// inline stroke below reads --ow-hairline directly and follows the theme.
export function GrecaDivider() {
  const patternId = useId();

  return (
    <svg aria-hidden="true" width="100%" height={13} style={{ display: 'block' }}>
      <pattern
        id={patternId}
        x="0"
        y="0"
        width={24}
        height={13}
        patternUnits="userSpaceOnUse"
      >
        <path
          d="M0 11V7h6V3h6v4h6v4h6"
          fill="none"
          stroke="var(--ow-hairline)"
          strokeWidth="1.8"
        />
      </pattern>
      <rect width="100%" height={13} fill={`url(#${patternId})`} />
    </svg>
  );
}
