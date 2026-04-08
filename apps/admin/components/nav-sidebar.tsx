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
  GraduationCap,
  HardDrive,
  UserCog,
  Bug,
  Eye,
  FileBarChart,
  Cog,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useSidebar, SidebarToggle } from '@sagecurator/ui';
import { ThemeToggle } from './theme-toggle';
import { isCloud } from '@/utils/mode';
import { useRole } from '@/hooks/use-role';
import { WorkspaceSwitcher } from './workspace-switcher';

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
  /** If true, render at the bottom of the sidebar (separated from main groups) */
  bottom?: boolean;
}

/* ──────────────────────────────────────────────────────────────────────────
 * Master group list. All possible groups across all roles.
 * The useRole() hook determines which groups are visible for the current user.
 * Group IDs MUST match the keys in ROLE_NAV_GROUPS in utils/roles.ts.
 * ────────────────────────────────────────────────────────────────────────── */

const ALL_GROUPS: NavGroup[] = [
  /* ── HOME ── */
  {
    id: 'home',
    label: 'Home',
    icon: LayoutDashboard,
    defaultHref: '/',
    items: [
      { href: '/', label: 'Dashboard' },
      { href: '/playground', label: 'Playground' },
    ],
  },

  /* ── BUILD (admin, developer) ── */
  {
    id: 'build',
    label: 'Build',
    icon: Bot,
    defaultHref: '/agents',
    items: [
      { href: '/agents', label: 'Agent Registry' },
      { href: '/agents/templates', label: 'Agent Templates' },
      { href: '/agents/runs', label: 'Agent Runs' },
      { href: '/workflows', label: 'Workflow Builder' },
      { href: '/workflows/registry', label: 'Workflow Registry' },
      { href: '/workflows/history', label: 'Workflow History' },
    ],
  },

  /* ── INTELLIGENCE (admin, developer, ml_engineer) ── */
  {
    id: 'intelligence',
    label: 'Intelligence',
    icon: Brain,
    defaultHref: '/context',
    items: [
      { href: '/context', label: 'Context Engine' },
      { href: '/context/documents', label: 'Documents' },
      { href: '/context/search', label: 'Search' },
      { href: '/memory/vector', label: 'Vector Store' },
      { href: '/memory/graph', label: 'Knowledge Graph' },
      { href: '/context/directives', label: 'Directives' },
    ],
  },

  /* ── OPERATIONS (admin) ── */
  {
    id: 'operations',
    label: 'Operations',
    icon: Server,
    defaultHref: '/fleet',
    items: [
      { href: '/fleet', label: 'Fleet Workers' },
      { href: '/fleet/enrollment-keys', label: 'Enrollment Keys' },
      { href: '/workflows/dispatch', label: 'Dispatch' },
      { href: '/workflows/approvals', label: 'Approvals' },
      { href: '/workflows/dlq', label: 'Failed Workflows' },
    ],
  },

  /* ── OBSERVE (admin) ── */
  {
    id: 'observe',
    label: 'Observe',
    icon: BarChart2,
    defaultHref: '/analytics/costs',
    items: [
      { href: '/analytics/costs', label: 'Cost Analytics' },
      { href: '/analytics/models', label: 'Model Comparison' },
      { href: '/analytics/performance', label: 'Performance' },
      { href: '/safety/audit', label: 'Audit Log' },
      { href: '/compliance/pii', label: 'PII Dashboard' },
      { href: '/intelligence/spend', label: 'LLM Spend' },
    ],
  },

  /* ── HARNESS (admin) ── */
  {
    id: 'harness',
    label: 'Harness',
    icon: Gauge,
    defaultHref: '/harness',
    items: [
      { href: '/harness', label: 'Dashboard' },
      { href: '/harness/policies', label: 'Policies' },
      { href: '/harness/keys', label: 'Keys' },
      { href: '/harness/analytics', label: 'Analytics' },
    ],
  },

  /* ── TOOLS (developer) ── */
  {
    id: 'tools',
    label: 'Tools',
    icon: Wrench,
    defaultHref: '/playground',
    items: [
      { href: '/tools/mcp', label: 'MCP Servers' },
      { href: '/strategy-lab', label: 'Strategy Lab' },
      { href: '/tools/ollama', label: 'Ollama' },
      { href: '/tools/model-router', label: 'Model Router' },
    ],
  },

  /* ── DEBUG (developer) ── */
  {
    id: 'debug',
    label: 'Debug',
    icon: Bug,
    defaultHref: '/monitor',
    items: [
      { href: '/monitor', label: 'Execution Monitor' },
      { href: '/observability/prompts', label: 'Prompt History' },
      { href: '/analytics/network', label: 'Agent Network' },
    ],
  },

  /* ── TRAINING (ml_engineer) ── */
  {
    id: 'training',
    label: 'Training',
    icon: GraduationCap,
    defaultHref: '/training/logs',
    items: [
      { href: '/training/logs', label: 'Run Logs' },
      { href: '/training/corpus', label: 'Corpus Builder' },
      { href: '/training/evals', label: 'Evaluations' },
      { href: '/training/finetune', label: 'Fine-Tuning Jobs' },
    ],
  },

  /* ── DATA (ml_engineer) ── */
  {
    id: 'data',
    label: 'Data',
    icon: HardDrive,
    defaultHref: '/data/storage',
    items: [
      { href: '/data/storage', label: 'Storage Management' },
      { href: '/data/quality', label: 'Data Quality' },
    ],
  },

  /* ── ANALYTICS (ml_engineer) ── */
  {
    id: 'analytics',
    label: 'Analytics',
    icon: FileBarChart,
    defaultHref: '/analytics/models',
    items: [
      { href: '/analytics/models', label: 'Model Comparison' },
      { href: '/analytics/costs', label: 'Cost Analytics' },
      { href: '/analytics/performance', label: 'Agent Performance' },
      { href: '/intelligence/spend', label: 'LLM Spend' },
    ],
  },

  /* ── REPORTS (viewer) ── */
  {
    id: 'reports',
    label: 'Reports',
    icon: Eye,
    defaultHref: '/analytics/costs',
    items: [
      { href: '/analytics/costs', label: 'Cost Analytics' },
      { href: '/analytics/models', label: 'Model Comparison' },
      { href: '/analytics/performance', label: 'Agent Performance' },
      { href: '/intelligence/spend', label: 'LLM Spend' },
    ],
  },

  /* ── AGENTS read-only (viewer) ── */
  {
    id: 'agents-readonly',
    label: 'Agents',
    icon: Bot,
    defaultHref: '/agents',
    items: [
      { href: '/agents', label: 'Agent Registry' },
      { href: '/agents/runs', label: 'Agent Runs' },
      { href: '/workflows/history', label: 'Workflow History' },
    ],
  },

  /* ── SYSTEM (admin only) ── */
  {
    id: 'system',
    label: 'System',
    icon: Cog,
    defaultHref: '/system/organization',
    items: [
      { href: '/system/organization', label: 'Organization' },
      { href: '/system/models', label: 'AI Models & Providers' },
      { href: '/system/connectors', label: 'Connectors' },
      { href: '/system/infrastructure', label: 'Infrastructure' },
      { href: '/system/projects', label: 'Projects' },
      { href: '/system/billing', label: 'Billing' },
      { href: '/system/notifications', label: 'Notifications' },
      { href: '/system/health', label: 'System Health' },
    ],
    bottom: true,
  },

  /* ── MY ACCOUNT (all roles) ── */
  {
    id: 'account',
    label: 'My Account',
    icon: UserCog,
    defaultHref: '/account/profile',
    items: [
      { href: '/account/profile', label: 'Profile & Password' },
      { href: '/account/tokens', label: 'API Tokens' },
      { href: '/account/security', label: '2FA Security' },
    ],
    bottom: true,
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

/* ── Legacy settings group (kept for backward compatibility with redirects) ── */
const LEGACY_SETTINGS_ROUTES = [
  '/settings/organization', '/settings/account', '/settings/tokens',
  '/settings/models', '/settings/services', '/settings/triggers',
  '/settings/projects', '/settings/billing', '/settings/infrastructure',
  '/settings/notifications', '/settings/health',
];

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
            : 'border-transparent text-text-on-dark/65 hover:bg-white/[0.08] hover:text-text-on-dark'
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
          <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-widest text-text-on-dark/60 border-b border-white/5 mb-1">
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
                    : 'text-text-on-dark/75 hover:text-text-on-dark hover:bg-white/5'
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
  // Handle legacy settings routes → treat as active for system/account groups
  if (LEGACY_SETTINGS_ROUTES.some((r) => pathname === r || pathname.startsWith(r + '/'))) {
    if (href.startsWith('/system/') || href.startsWith('/account/')) {
      return false; // Let the redirect handle it
    }
  }
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
  const { navGroups: allowedGroupIds, favorites } = useRole();

  // Filter groups by role
  const roleGroups = ALL_GROUPS.filter((g) => allowedGroupIds.includes(g.id));
  const cloudGroups = isCloud ? [...roleGroups, WORKSPACE_GROUP] : roleGroups;

  // Separate main groups from bottom groups
  const mainGroups = cloudGroups.filter((g) => !g.bottom);
  const bottomGroups = cloudGroups.filter((g) => g.bottom);

  const allGroups = [...mainGroups, ...bottomGroups];
  const activeGroupId = getActiveGroupId(pathname, allGroups);

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

  function renderGroup(group: NavGroup) {
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
      <div key={group.id} className="mb-0.5">
        <button
          onClick={() => toggleGroup(group.id)}
          aria-expanded={!isCollapsed}
          className={`w-full flex items-center gap-2.5 px-5 py-2.5 text-[11px] font-semibold uppercase tracking-wider transition-colors cursor-pointer bg-transparent border-none min-h-[36px] ${
            isGroupActive ? 'text-primary' : 'text-text-on-dark/60 hover:text-text-on-dark/80'
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
                  : 'text-text-on-dark/75 border-transparent hover:text-text-on-dark hover:bg-white/5'
              }`}
            >
              {label}
            </Link>
          );
        })}
      </div>
    );
  }

  return (
    <>
      {/* Header */}
      <div className={`flex items-center justify-between shrink-0 ${expanded ? 'px-5 py-4' : 'px-3 py-4'}`}>
        {expanded ? (
          <Link href="/" className="flex items-center gap-2.5 no-underline">
            <img src="/brand/logo.svg" alt="Sagewai" className="h-7 w-7" />
            <span className="text-base font-bold font-[family-name:var(--font-heading)] tracking-wide bg-clip-text text-transparent" style={{ backgroundImage: 'var(--gradient-brand)' }}>
              SAGEWAI
            </span>
          </Link>
        ) : (
          <Link href="/" className="mx-auto no-underline">
            <img src="/brand/logo.svg" alt="Sagewai" className="h-7 w-7" />
          </Link>
        )}
        <div className="flex items-center gap-0.5">
          <ThemeToggle />
          <SidebarToggle />
        </div>
      </div>

      {/* Workspace switcher — cloud + expanded only */}
      {isCloud && expanded && (
        <div className="px-5 mb-3 shrink-0">
          <WorkspaceSwitcher />
        </div>
      )}

      {/* Divider */}
      {expanded && <div className="mx-5 mb-3 border-t border-white/8" />}

      {/* Main navigation groups */}
      <nav className="flex-1 overflow-y-auto pb-md" aria-label="Main navigation">
        {mainGroups.map(renderGroup)}

        {/* Separator before bottom groups */}
        {bottomGroups.length > 0 && (
          <div className="mx-5 my-2 border-t border-white/10" />
        )}
        {bottomGroups.map(renderGroup)}
      </nav>
    </>
  );
}
