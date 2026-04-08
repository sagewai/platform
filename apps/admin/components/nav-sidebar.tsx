'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutDashboard,
  Bot,
  GitBranch,
  BarChart2,
  ShieldCheck,
  Brain,
  Database,
  Wrench,
  Server,
  Gauge,
  Settings2,
  Building2,
  ChevronDown,
  ChevronRight,
  Star,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useSidebar, SidebarToggle, ThemeToggle } from '@sagecurator/ui';
import { isCloud } from '@/utils/mode';
import { WorkspaceSwitcher } from './workspace-switcher';

const FAVORITES = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/agents', label: 'Agents', icon: Bot },
  { href: '/playground', label: 'Playground', icon: Wrench },
  { href: '/settings/models', label: 'Settings', icon: Settings2 },
] as const;

const LS_KEY = 'nav-groups-collapsed';

interface NavItem {
  href: string;
  label: string;
}

interface NavGroup {
  id: string;
  label: string;
  icon: LucideIcon;
  /** Destination when icon is tapped in collapsed mode */
  defaultHref: string;
  items: NavItem[];
}

const GROUPS: NavGroup[] = [
  {
    id: 'home',
    label: 'Home',
    icon: LayoutDashboard,
    defaultHref: '/',
    items: [{ href: '/', label: 'Dashboard' }],
  },
  {
    id: 'agents',
    label: 'Agents',
    icon: Bot,
    defaultHref: '/agents',
    items: [
      { href: '/agents', label: 'Agent Registry' },
      { href: '/agents/templates', label: 'Agent Templates' },
      { href: '/agents/runs', label: 'Agent Runs' },
    ],
  },
  {
    id: 'workflows',
    label: 'Workflows',
    icon: GitBranch,
    defaultHref: '/workflows',
    items: [
      { href: '/workflows', label: 'Workflow Builder' },
      { href: '/workflows/registry', label: 'Registry' },
      { href: '/workflows/history', label: 'Workflow History' },
      { href: '/workflows/templates', label: 'Workflow Templates' },
      { href: '/workflows/dispatch', label: 'Dispatch' },
      { href: '/workflows/workers', label: 'Workers' },
      { href: '/workflows/dlq', label: 'Failed Workflows' },
      { href: '/workflows/approvals', label: 'Approvals' },
    ],
  },
  {
    id: 'observe',
    label: 'Observe',
    icon: BarChart2,
    defaultHref: '/analytics/costs',
    items: [
      { href: '/analytics/costs', label: 'Cost Analytics' },
      { href: '/analytics/models', label: 'Model Comparison' },
      { href: '/analytics/network', label: 'Agent Network' },
      { href: '/analytics/performance', label: 'Performance' },
      { href: '/monitor', label: 'Execution Monitor' },
      { href: '/observability/prompts', label: 'Prompt History' },
      { href: '/eval/datasets', label: 'Datasets' },
      { href: '/eval/run', label: 'Run Eval' },
      { href: '/eval/reports', label: 'Reports' },
    ],
  },
  {
    id: 'safety',
    label: 'Safety',
    icon: ShieldCheck,
    defaultHref: '/safety/guardrails',
    items: [
      { href: '/safety/guardrails', label: 'Guardrails' },
      { href: '/safety/audit', label: 'Audit Log' },
      { href: '/compliance/pii', label: 'PII Dashboard' },
    ],
  },
  {
    id: 'intelligence',
    label: 'Intelligence',
    icon: Brain,
    defaultHref: '/intelligence/dashboard',
    items: [
      { href: '/intelligence/dashboard', label: 'Dashboard' },
      { href: '/context', label: 'Context Engine' },
      { href: '/context/documents', label: 'Documents' },
      { href: '/context/search', label: 'Search' },
      { href: '/context/lifecycle', label: 'Lifecycle' },
      { href: '/context/directives', label: 'Directives' },
      { href: '/intelligence/spend', label: 'LLM Spend' },
    ],
  },
  {
    id: 'memory',
    label: 'Memory',
    icon: Database,
    defaultHref: '/memory/vector',
    items: [
      { href: '/memory/vector', label: 'Vector Store' },
      { href: '/memory/graph', label: 'Knowledge Graph' },
    ],
  },
  {
    id: 'tools',
    label: 'Tools',
    icon: Wrench,
    defaultHref: '/playground',
    items: [
      { href: '/tools/mcp', label: 'MCP Servers' },
      { href: '/tools/model-router', label: 'Model Router' },
      { href: '/tools/ollama', label: 'Ollama' },
      { href: '/playground', label: 'Playground' },
      { href: '/strategy-lab', label: 'Strategy Lab' },
    ],
  },
  {
    id: 'fleet',
    label: 'Fleet',
    icon: Server,
    defaultHref: '/fleet',
    items: [
      { href: '/fleet', label: 'Workers' },
      { href: '/fleet/enrollment-keys', label: 'Enrollment Keys' },
      { href: '/fleet/audit', label: 'Audit Log' },
    ],
  },
  {
    id: 'harness',
    label: 'LLM Harness',
    icon: Gauge,
    defaultHref: '/harness',
    items: [
      { href: '/harness', label: 'Dashboard' },
      { href: '/harness/policies', label: 'Policies' },
      { href: '/harness/keys', label: 'Keys' },
      { href: '/harness/analytics', label: 'Analytics' },
    ],
  },
  {
    id: 'settings',
    label: 'Settings',
    icon: Settings2,
    defaultHref: '/settings/organization',
    items: [
      { href: '/settings/organization', label: 'Organization' },
      { href: '/settings/account', label: 'Account' },
      { href: '/settings/tokens', label: 'API Tokens' },
      { href: '/settings/models', label: 'AI Models' },
      { href: '/settings/services', label: 'Connectors' },
      { href: '/settings/triggers', label: 'Triggers' },
      { href: '/settings/projects', label: 'Projects' },
      { href: '/operations/budget', label: 'Budget' },
      { href: '/settings/billing', label: 'Billing' },
      { href: '/settings/infrastructure', label: 'Infrastructure' },
      { href: '/settings/notifications', label: 'Notifications' },
      { href: '/settings/health', label: 'System Health' },
    ],
  },
];

const WORKSPACE_GROUP: NavGroup = {
  id: 'workspace',
  label: 'Workspace',
  icon: Building2,
  defaultHref: '/workspace/settings',
  items: [
    { href: '/workspace/settings', label: 'Settings' },
    { href: '/workspace/members', label: 'Members' },
    { href: '/workspace/teams', label: 'Teams' },
    { href: '/workspace/providers', label: 'LLM Providers' },
  ],
};

/** Collapsed-mode icon with fixed-position flyout menu (not clipped by overflow). */
function NavIconWithFlyout({
  group,
  isGroupActive,
  pathname,
}: {
  group: NavGroup;
  isGroupActive: boolean;
  pathname: string;
}) {
  const Icon = group.icon;
  const btnRef = useRef<HTMLButtonElement>(null);
  const flyoutRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  const updatePos = useCallback(() => {
    if (!btnRef.current) return;
    const rect = btnRef.current.getBoundingClientRect();
    setPos({ top: rect.top, left: rect.right + 8 });
  }, []);

  useEffect(() => {
    if (!open) return;
    updatePos();
    window.addEventListener('scroll', updatePos, true);
    window.addEventListener('resize', updatePos);
    return () => {
      window.removeEventListener('scroll', updatePos, true);
      window.removeEventListener('resize', updatePos);
    };
  }, [open, updatePos]);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent | TouchEvent) {
      const target = e.target as Node;
      if (btnRef.current?.contains(target) || flyoutRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
    };
  }, [open]);

  // Close on route change
  useEffect(() => { setOpen(false); }, [pathname]);

  return (
    <div className="px-2 py-0.5">
      <button
        ref={btnRef}
        type="button"
        aria-label={group.label}
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={() => { setOpen(true); updatePos(); }}
        onMouseLeave={(e) => {
          // Don't close if moving to flyout
          const related = e.relatedTarget as Node | null;
          if (flyoutRef.current?.contains(related)) return;
          setOpen(false);
        }}
        className={`flex items-center justify-center w-full h-11 rounded-md transition-colors border-l-[3px] cursor-pointer bg-transparent ${
          isGroupActive
            ? 'bg-white/10 border-primary text-white'
            : 'border-transparent text-text-on-dark/50 hover:bg-white/[0.08] hover:text-text-on-dark'
        }`}
      >
        <Icon size={18} strokeWidth={isGroupActive ? 2 : 1.5} aria-hidden="true" />
      </button>

      {open && typeof document !== 'undefined' && createPortal(
        <div
          ref={flyoutRef}
          onMouseLeave={(e) => {
            const related = e.relatedTarget as Node | null;
            if (btnRef.current?.contains(related)) return;
            setOpen(false);
          }}
          style={{ position: 'fixed', top: pos.top, left: pos.left, zIndex: 9999 }}
          className="bg-bg-elevated border border-white/10 rounded-lg shadow-xl min-w-[180px] py-1.5"
        >
          <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-widest text-text-on-dark/40 border-b border-white/5 mb-1">
            {group.label}
          </div>
          {group.items.map(({ href, label }) => {
            const siblingHrefs = group.items.map((i) => i.href);
            const active = isItemActive(pathname, href, siblingHrefs);
            return (
              <Link
                key={href}
                href={href}
                onClick={() => setOpen(false)}
                className={`block px-3 py-2 text-[13px] no-underline transition-colors ${
                  active
                    ? 'text-white bg-white/10 font-semibold'
                    : 'text-text-on-dark/60 hover:text-text-on-dark hover:bg-white/5'
                }`}
              >
                {label}
              </Link>
            );
          })}
        </div>,
        document.body,
      )}
    </div>
  );
}

function isItemActive(pathname: string, href: string, siblings?: string[]): boolean {
  if (href === '/') return pathname === '/';
  const matches = pathname === href || pathname.startsWith(href + '/');
  if (!matches) return false;
  // If a sibling item is a longer (more specific) match, this item should not be active
  if (siblings) {
    for (const s of siblings) {
      if (s !== href && s.length > href.length && (pathname === s || pathname.startsWith(s + '/'))) {
        return false;
      }
    }
  }
  return true;
}

function getActiveGroupId(pathname: string, groups: NavGroup[]): string | null {
  for (const group of groups) {
    for (const item of group.items) {
      if (isItemActive(pathname, item.href)) return group.id;
    }
  }
  return null;
}

export function NavSidebar() {
  const pathname = usePathname();
  const { expanded, setExpanded, mobile } = useSidebar();
  const groups = isCloud ? [...GROUPS, WORKSPACE_GROUP] : GROUPS;
  const activeGroupId = getActiveGroupId(pathname, groups);

  /** On mobile, close sidebar when navigating */
  const closeMobile = useCallback(() => {
    if (mobile) setExpanded(false);
  }, [mobile, setExpanded]);

  // Collapsed groups state — persisted in localStorage
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  useEffect(() => {
    try {
      const stored = localStorage.getItem(LS_KEY);
      if (stored) setCollapsedGroups(new Set(JSON.parse(stored) as string[]));
    } catch {
      // ignore
    }
  }, []);

  function toggleGroup(id: string) {
    setCollapsedGroups((prev: Set<string>) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      try {
        localStorage.setItem(LS_KEY, JSON.stringify([...next]));
      } catch {
        // ignore
      }
      return next;
    });
  }

  return (
    <>
      {/* Header */}
      <div className={`flex items-center justify-between py-lg shrink-0 ${expanded ? 'px-5' : 'px-3'}`}>
        {expanded ? (
          <div>
            <h2 className="m-0 text-lg leading-tight font-[family-name:var(--font-heading)] font-bold tracking-wide">
              <span className="bg-clip-text text-transparent" style={{ backgroundImage: 'var(--gradient-brand)' }}>SAGEWAI</span>
            </h2>
            <div className="text-[10px] font-semibold mt-0.5 font-[family-name:var(--font-mono)] tracking-wide text-primary/70">
              Operations Console
            </div>
          </div>
        ) : (
          <span className="mx-auto text-sm font-bold font-[family-name:var(--font-heading)]">
            <span className="bg-clip-text text-transparent" style={{ backgroundImage: 'var(--gradient-brand)' }}>SW</span>
          </span>
        )}
        <div className="flex items-center gap-1">
          <ThemeToggle />
          <SidebarToggle />
        </div>
      </div>

      {/* Workspace switcher — cloud + expanded only */}
      {isCloud && expanded && (
        <div className="px-5 mb-md shrink-0">
          <WorkspaceSwitcher />
        </div>
      )}

      {/* Favorites strip — expanded only */}
      {expanded && (
        <div className="px-3 mb-3 shrink-0">
          <div className="flex items-center gap-1.5 px-2 mb-1.5">
            <Star size={10} strokeWidth={2.5} className="text-text-on-dark/30" aria-hidden="true" />
            <span className="text-[10px] font-semibold uppercase tracking-widest text-text-on-dark/30">
              Favorites
            </span>
          </div>
          <div className="flex gap-1">
            {FAVORITES.map(({ href, label, icon: FavIcon }) => {
              const active = isItemActive(pathname, href);
              return (
                <Link
                  key={href}
                  href={href}
                  title={label}
                  onClick={closeMobile}
                  className={`flex-1 flex flex-col items-center gap-1 py-2 rounded-md text-center transition-colors no-underline ${
                    active
                      ? 'bg-white/10 text-white'
                      : 'text-text-on-dark/50 hover:bg-white/[0.08] hover:text-text-on-dark'
                  }`}
                >
                  <FavIcon size={14} strokeWidth={active ? 2 : 1.5} aria-hidden="true" />
                  <span className="text-[10px] leading-none">{label}</span>
                </Link>
              );
            })}
          </div>
        </div>
      )}

      {/* Navigation groups */}
      <nav className="flex-1 overflow-y-auto pb-md" aria-label="Main navigation">
        {groups.map((group) => {
          const Icon = group.icon;
          const isGroupActive = activeGroupId === group.id;
          const isCollapsed = collapsedGroups.has(group.id);

          /* ── Collapsed sidebar: icon with flyout (portalled to body) ── */
          if (!expanded) {
            return (
              <NavIconWithFlyout
                key={group.id}
                group={group}
                isGroupActive={isGroupActive}
                pathname={pathname}
              />
            );
          }

          /* ── Expanded sidebar: group header + collapsible sub-items ── */
          return (
            <div key={group.id} className="mb-1">
              <button
                onClick={() => toggleGroup(group.id)}
                aria-expanded={!isCollapsed}
                className={`w-full flex items-center gap-2 px-5 py-1.5 text-[11px] font-semibold uppercase tracking-widest transition-colors ${
                  isGroupActive ? 'text-primary' : 'text-text-on-dark/35 hover:text-text-on-dark/60'
                }`}
              >
                <Icon size={12} strokeWidth={2.5} aria-hidden="true" />
                <span className="flex-1 text-left">{group.label}</span>
                {isCollapsed ? (
                  <ChevronRight size={10} strokeWidth={2.5} aria-hidden="true" />
                ) : (
                  <ChevronDown size={10} strokeWidth={2.5} aria-hidden="true" />
                )}
              </button>

              {!isCollapsed && group.items.map(({ href, label }) => {
                const siblingHrefs = group.items.map((i) => i.href);
                const active = isItemActive(pathname, href, siblingHrefs);
                const tourAttr = href === '/agents' ? 'nav-agents' : href === '/workflows' ? 'nav-workflows' : undefined;
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={closeMobile}
                    {...(tourAttr ? { 'data-tour': tourAttr } : {})}
                    className={`block px-5 py-2 text-[13px] no-underline transition-colors border-l-[3px] ${
                      active
                        ? 'text-white bg-white/10 border-primary font-semibold'
                        : 'text-text-on-dark/60 border-transparent hover:text-text-on-dark hover:bg-white/5'
                    }`}
                  >
                    {label}
                  </Link>
                );
              })}
            </div>
          );
        })}
      </nav>
    </>
  );
}
