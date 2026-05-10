'use client';

import confetti from 'canvas-confetti';
import { useEffect, useRef } from 'react';
import { consumeFirstMissionFlag } from '@/app/autopilot/missions/[id]/actions';
import { useReducedMotion } from '@/hooks/use-reduced-motion';

// Brand palette — these are static decoration values, not theme tokens.
// Color-invariant: confetti is a one-shot celebration, not a UI element.
const BRAND_COLORS = ['#FF6B35', '#F7C59F', '#EFEFD0', '#1A659E', '#004E89'];

export function FirstSuccessCelebration({
  status,
  anchorRef,
}: {
  status: string;
  anchorRef: React.RefObject<HTMLElement | null>;
}) {
  const fired = useRef(false);
  const reduced = useReducedMotion();

  useEffect(() => {
    if (status !== 'completed' || fired.current || reduced) return;
    fired.current = true;

    void (async () => {
      const should = await consumeFirstMissionFlag();
      if (!should) return;

      const rect = anchorRef.current?.getBoundingClientRect();
      const origin = rect
        ? {
            x: (rect.left + rect.width / 2) / window.innerWidth,
            y: (rect.top + rect.height / 2) / window.innerHeight,
          }
        : { x: 0.5, y: 0.6 };

      confetti({
        particleCount: 80,
        spread: 65,
        startVelocity: 35,
        origin,
        colors: BRAND_COLORS,
        disableForReducedMotion: true,
      });
    })();
  }, [status, anchorRef, reduced]);

  return null;
}
