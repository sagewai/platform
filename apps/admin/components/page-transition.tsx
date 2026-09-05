'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useRef } from 'react';

/**
 * Replay the page-enter animation on every navigation without remounting the page tree.
 *
 * A `key={pathname}` here would remount every nested layout on each navigation — the Task
 * shell would re-read its detail, drop its feed and flash its skeleton on every tab switch —
 * so the animation is restarted in place instead. `getAnimations()` still holds the finished
 * animation only because the shorthand ends in `both`; without a fill mode it would be gone.
 */
export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const ref = useRef<HTMLDivElement>(null);
  const prevPath = useRef(pathname);

  useEffect(() => {
    if (prevPath.current === pathname) return;
    prevPath.current = pathname;
    for (const animation of ref.current?.getAnimations() ?? []) {
      animation.cancel();
      animation.play();
    }
  }, [pathname]);

  return (
    <div ref={ref} style={{ animation: 'page-enter 150ms var(--ease-enter) both' }}>
      {children}
    </div>
  );
}
