'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { motion } from 'framer-motion';
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
  Search,
  Zap,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useSidebar, SidebarToggle } from '@/components/ui/legacy';
import { ThemeToggle } from './theme-toggle';
import { isCloud } from '@/utils/mode';
import { useRole } from '@/hooks/use-role';
import { WorkspaceSwitcher } from './workspace-switcher';
import { openCommandPalette } from './command-palette';

const LS_KEY = 'nav-groups-collapsed';

export interface NavItem {
  href: string;
  label: string;
}

export interface NavGroup {
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
 * Exported so the command palette can reuse the same source of truth.
 * Group IDs MUST match the keys in ROLE_NAV_GROUPS in utils/roles.ts.
 * ────────────────────────────────────────────────────────────────────────── */

export const ALL_GROUPS: NavGroup[] = [
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

  /* ── BUILD ── */
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

  /* ── AUTOPILOT ── */
  {
    id: 'autopilot',
    label: 'Autopilot (beta)',
    icon: Zap,
    defaultHref: '/autopilot',
    items: [
      { href: '/autopilot', label: 'Autopilot (beta)' },
    ],
  },

  /* ── INTELLIGENCE ── */
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

  /* ── OPERATIONS ── */
  {
    id: 'operations',
    label: 'Operations',
    icon: Server,
    defaultHref: '/fleet',
    items: [
      { href: '/fleet', label: 'Fleet Workers' },
      { href: '/fleet/enrollment-keys', label: 'Enrollment Keys' },
      { href: '/connections', label: 'Connections' },
      { href: '/workflows/dispatch', label: 'Dispatch' },
      { href: '/workflows/approvals', label: 'Approvals' },
      { href: '/workflows/dlq', label: 'Failed Workflows' },
    ],
  },

  /* ── OBSERVE ── */
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

  /* ── HARNESS ── */
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

  /* ── TOOLS ── */
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

  /* ── DEBUG ── */
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

  /* ── TRAINING ── */
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

  /* ── DATA ── */
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

  /* ── ANALYTICS ── */
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

  /* ── REPORTS ── */
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

  /* ── AGENTS read-only ── */
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

  /* ── SYSTEM ── */
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
      { href: '/system/notifications', label: 'Notifications' },
      { href: '/system/health', label: 'System Health' },
    ],
    bottom: true,
  },

  /* ── MY ACCOUNT ── */
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
  '/settings/projects', '/settings/infrastructure',
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
            ? 'bg-sidebar-accent border-sidebar-accent-foreground text-sidebar-accent-foreground'
            : 'border-transparent text-sidebar-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
        }`}
      >
        <Icon size={18} strokeWidth={isGroupActive ? 2 : 1.5} aria-hidden="true" />
      </button>

      {open && typeof document !== 'undefined' && createPortal(
        <motion.div
          ref={flyoutRef}
          initial={{ opacity: 0, x: -8 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.12, ease: 'easeOut' }}
          onMouseLeave={(e) => {
            const related = e.relatedTarget as Node | null;
            if (btnRef.current?.contains(related)) return;
            setOpen(false);
          }}
          style={{ position: 'fixed', top: pos.top, left: pos.left, zIndex: 9999 }}
          className="bg-popover text-popover-foreground border border-border rounded-lg shadow-xl min-w-[180px] py-1.5 backdrop-blur-md"
        >
          <div className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground border-b border-border mb-1">
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
                    ? 'text-sidebar-accent-foreground bg-sidebar-accent font-semibold'
                    : 'text-popover-foreground/80 hover:text-popover-foreground hover:bg-accent/40'
                }`}
              >
                {label}
              </Link>
            );
          })}
        </motion.div>,
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
  const { navGroups: allowedGroupIds } = useRole();

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
          className={`w-full flex items-center gap-2.5 px-5 py-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] transition-colors cursor-pointer bg-transparent border-none min-h-[36px] ${
            isGroupActive
              ? 'text-sidebar-accent-foreground'
              : 'text-sidebar-muted-foreground hover:text-sidebar-foreground'
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
                  ? 'text-sidebar-accent-foreground bg-sidebar-accent border-sidebar-accent-foreground font-semibold'
                  : 'text-sidebar-foreground/80 border-transparent hover:text-sidebar-foreground hover:bg-sidebar-accent/40'
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
      {/* Header — only logo + collapse toggle (theme toggle moved to footer) */}
      <div className={`flex items-center justify-between shrink-0 ${expanded ? 'px-5 py-4' : 'px-3 py-4'}`}>
        {expanded ? (
          <Link href="/" className="flex items-center no-underline" aria-label="Sagewai home">
            {/* Full logo (includes wordmark). Light variant for light mode,
                dark variant for dark mode. */}
            <img
              src="/brand/sagewai_logo.svg"
              alt="Sagewai"
              className="h-7 w-auto block dark:hidden"
            />
            <img
              src="/brand/sagewai_logo_dark.svg"
              alt="Sagewai"
              className="h-7 w-auto hidden dark:block"
            />
          </Link>
        ) : (
          <Link href="/" className="mx-auto no-underline" aria-label="Sagewai home">
            <img
              src="/brand/sagewai_icon.svg"
              alt="Sagewai"
              className="h-7 w-7 block dark:hidden"
            />
            <img
              src="/brand/sagewai_icon_dark.svg"
              alt="Sagewai"
              className="h-7 w-7 hidden dark:block"
            />
          </Link>
        )}
        {expanded && <SidebarToggle />}
      </div>

      {/* Search / command palette trigger (expanded only) */}
      {expanded && (
        <div className="px-5 mb-3 shrink-0">
          <button
            type="button"
            onClick={openCommandPalette}
            className="w-full flex items-center gap-2 px-3 py-2 text-[13px] rounded-md border border-sidebar-border bg-sidebar-accent/30 text-sidebar-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent/60 transition-colors cursor-pointer"
          >
            <Search size={14} aria-hidden="true" />
            <span className="flex-1 text-left">Search…</span>
            <kbd className="text-[10px] font-semibold tracking-wider px-1.5 py-0.5 rounded border border-sidebar-border bg-sidebar/40">
              ⌘K
            </kbd>
          </button>
        </div>
      )}

      {/* Workspace switcher — cloud + expanded only */}
      {isCloud && expanded && (
        <div className="px-5 mb-3 shrink-0">
          <WorkspaceSwitcher />
        </div>
      )}

      {/* Divider */}
      {expanded && <div className="mx-5 mb-3 border-t border-sidebar-border" />}

      {/* Main navigation groups */}
      <nav className="flex-1 overflow-y-auto pb-md" aria-label="Main navigation">
        {mainGroups.map(renderGroup)}

        {/* Separator before bottom groups */}
        {bottomGroups.length > 0 && (
          <div className="mx-5 my-2 border-t border-sidebar-border" />
        )}
        {bottomGroups.map(renderGroup)}
      </nav>

      {/* Footer — theme toggle + collapse toggle (collapsed mode only) */}
      <div
        className={`shrink-0 border-t border-sidebar-border flex items-center ${
          expanded ? 'px-5 py-3 justify-between' : 'px-2 py-3 flex-col gap-2'
        }`}
      >
        {expanded && <span className="text-[11px] text-sidebar-muted-foreground">Theme</span>}
        <ThemeToggle />
        {!expanded && <SidebarToggle />}
      </div>
    </>
  );
}
