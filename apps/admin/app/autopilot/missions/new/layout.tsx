'use client';

import { type ReactNode } from 'react';
import { AnimatePresence, LayoutGroup, motion } from 'framer-motion';
import { useSelectedLayoutSegment } from 'next/navigation';
import { useReducedMotion } from '@/hooks/use-reduced-motion';

export default function MissionFlowLayout({ children }: { children: ReactNode }) {
  const segment = useSelectedLayoutSegment() ?? 'root';
  const reduced = useReducedMotion();
  const transition = reduced
    ? { duration: 0 }
    : { type: 'spring' as const, stiffness: 320, damping: 30 };

  return (
    <LayoutGroup>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={segment}
          initial={{ opacity: 0, y: reduced ? 0 : 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: reduced ? 0 : -8 }}
          transition={transition}
          className="autopilot-flow-shell"
        >
          {children}
        </motion.div>
      </AnimatePresence>
    </LayoutGroup>
  );
}
