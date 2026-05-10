'use client';

import { cn } from '@/lib/utils';

export function Spinner({
  size = 20,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      role="status"
      aria-label="Loading"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      // motion-safe: spin; prefers-reduced-motion: pulse opacity instead
      className={cn(
        'motion-safe:animate-[spin_0.9s_cubic-bezier(0.4,0,0.2,1)_infinite]',
        'motion-reduce:animate-[pulse_1.5s_ease-in-out_infinite]',
        className,
      )}
    >
      <defs>
        <linearGradient id="sage-spinner-grad" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--color-primary)" />
          <stop offset="100%" stopColor="var(--color-accent-orange)" />
        </linearGradient>
      </defs>
      {/* Track ring */}
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="var(--color-border)"
        strokeWidth="3"
        fill="none"
      />
      {/* Gradient arc */}
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="url(#sage-spinner-grad)"
        strokeWidth="3"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}
