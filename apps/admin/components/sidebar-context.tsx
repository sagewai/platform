'use client';

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';

/**
 * Sidebar state — expand/collapse, mobile detection.
 *
 * Owned locally by admin now that @sagecurator/ui is decommissioned.
 * API matches the former `useSidebar` / `SidebarProvider` / `SidebarToggle`
 * exports so no call sites have to change logic.
 */
interface SidebarContextValue {
  expanded: boolean;
  setExpanded: (v: boolean) => void;
  mobile: boolean;
}

const SidebarContext = createContext<SidebarContextValue>({
  expanded: true,
  setExpanded: () => {},
  mobile: false,
});

export function useSidebar() {
  return useContext(SidebarContext);
}

export function SidebarProvider({ children }: { children: ReactNode }) {
  const [expanded, setExpanded] = useState(true);
  const [mobile, setMobile] = useState(false);

  useEffect(() => {
    function check() {
      const isMobile = window.innerWidth < 768;
      setMobile(isMobile);
      if (isMobile) setExpanded(false);
      else if (window.innerWidth < 1200) setExpanded(false);
      else setExpanded(true);
    }
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  return (
    <SidebarContext.Provider value={{ expanded, setExpanded, mobile }}>
      {children}
    </SidebarContext.Provider>
  );
}

export function SidebarToggle() {
  const { expanded, setExpanded } = useSidebar();
  const Icon = expanded ? PanelLeftClose : PanelLeftOpen;
  return (
    <button
      onClick={() => setExpanded(!expanded)}
      aria-label={expanded ? 'Collapse sidebar' : 'Expand sidebar'}
      className="w-8 h-8 flex items-center justify-center rounded-md text-sidebar-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors cursor-pointer bg-transparent border-none"
    >
      <Icon size={16} />
    </button>
  );
}
