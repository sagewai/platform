'use client';

import { usePathname } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

export function PageTransition({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [animating, setAnimating] = useState(false);
  const prevPath = useRef(pathname);

  useEffect(() => {
    if (prevPath.current !== pathname) {
      prevPath.current = pathname;
      setAnimating(true);
      const id = setTimeout(() => setAnimating(false), 150);
      return () => clearTimeout(id);
    }
  }, [pathname]);

  return (
    <div
      key={pathname}
      style={{
        animation: 'page-enter 150ms var(--ease-enter) both',
      }}
    >
      {children}
    </div>
  );
}
