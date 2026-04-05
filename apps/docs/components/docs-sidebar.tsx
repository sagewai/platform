'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

interface NavItem {
  label: string;
  href: string;
}

interface NavSection {
  title: string;
  items: NavItem[];
}

const SECTIONS: NavSection[] = [
  {
    title: 'Getting Started',
    items: [{ label: 'Introduction & Quickstart', href: '/docs/getting-started' }],
  },
  // ── Pillar: SDK ──────────────────────────────────────────────
  {
    title: 'SDK',
    items: [
      { label: 'Your First Agent', href: '/docs/guides/first-agent' },
      { label: 'Agents', href: '/docs/core-concepts/agents' },
      { label: 'Strategies', href: '/docs/core-concepts/strategies' },
      { label: 'Memory & RAG', href: '/docs/core-concepts/memory' },
      { label: 'Workflows', href: '/docs/core-concepts/workflows' },
      { label: 'Context Engine', href: '/docs/core-concepts/context-engine' },
      { label: 'Directives', href: '/docs/core-concepts/directives' },
      { label: 'Safety & Guardrails', href: '/docs/core-concepts/safety' },
      { label: 'Multi-Agent Workflows', href: '/docs/guides/multi-agent' },
      { label: 'Agent Patterns', href: '/docs/guides/patterns' },
    ],
  },
  // ── Pillar: Registry ─────────────────────────────────────────
  {
    title: 'Registry',
    items: [
      { label: 'Tools & MCP', href: '/docs/api-reference/tools' },
      { label: 'MCP Protocol', href: '/docs/api-reference/mcp' },
      { label: 'MCP Server', href: '/docs/guides/mcp-server' },
      { label: 'External Access', href: '/docs/guides/external-access' },
      { label: 'Gateway Streaming', href: '/docs/guides/gateway-streaming' },
    ],
  },
  // ── Pillar: Harness ──────────────────────────────────────────
  {
    title: 'Harness',
    items: [
      { label: 'LLM Harness', href: '/docs/guides/harness' },
      { label: 'Cost Management', href: '/docs/guides/cost-management' },
      { label: 'PII Protection', href: '/docs/guides/pii-protection' },
    ],
  },
  // ── Pillar: Observatory ──────────────────────────────────────
  {
    title: 'Observatory',
    items: [
      { label: 'Admin Panel', href: '/docs/guides/admin-panel' },
      { label: 'Notifications', href: '/docs/api-reference/notifications' },
      { label: 'REST API', href: '/docs/api-reference/rest-api' },
    ],
  },
  // ── Pillar: Training ─────────────────────────────────────────
  {
    title: 'Training',
    items: [
      { label: 'Self-Learning Agents', href: '/docs/core-concepts/self-learning' },
    ],
  },
  // ── Deployment & Reference ───────────────────────────────────
  {
    title: 'Deployment',
    items: [
      { label: 'Fleet Deployment', href: '/docs/guides/fleet' },
      { label: 'Fleet Architecture', href: '/docs/guides/fleet-architecture' },
      { label: 'Self-Hosted Deployment', href: '/docs/guides/self-hosted' },
    ],
  },
  {
    title: 'API Reference',
    items: [
      { label: 'Agents', href: '/docs/api-reference/agents' },
      { label: 'Strategies', href: '/docs/api-reference/strategies' },
      { label: 'Memory & Context', href: '/docs/api-reference/memory' },
      { label: 'Workflows', href: '/docs/api-reference/workflows' },
      { label: 'Safety & Guardrails', href: '/docs/api-reference/safety' },
      { label: 'Directives', href: '/docs/api-reference/directives' },
      { label: 'Self-Learning', href: '/docs/api-reference/self-learning' },
      { label: 'Project & Errors', href: '/docs/api-reference/project' },
    ],
  },
  {
    title: 'Comparisons',
    items: [
      { label: 'vs. MiniMax', href: '/docs/guides/vs-minimax' },
    ],
  },
];

export function DocsSidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-64 flex-shrink-0 border-r border-border bg-bg-surface overflow-y-auto h-[calc(100vh-64px)] sticky top-16">
      <nav className="p-4 space-y-6">
        {SECTIONS.map((section) => (
          <div key={section.title}>
            <h3 className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-2 px-3">
              {section.title}
            </h3>
            <ul className="space-y-0.5">
              {section.items.map((item) => {
                const isActive = pathname === item.href;
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={`block px-3 py-1.5 rounded-md text-sm transition-colors ${
                        isActive
                          ? 'bg-primary-light text-primary font-medium'
                          : 'text-text-secondary hover:text-text-primary hover:bg-bg-subtle'
                      }`}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
}
