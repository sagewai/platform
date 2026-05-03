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
    items: [
      { label: 'Introduction & Quickstart', href: '/docs/getting-started' },
      { label: 'Minimal Setup (No sandbox)', href: '/docs/getting-started/minimal-setup' },
      { label: 'Tutorials', href: '/docs/guides/tutorials' },
      { label: 'Video Tutorials', href: '/docs/guides/video-tutorials' },
    ],
  },
  // ── Architecture ──────────────────────────────────────────────
  {
    title: 'Architecture',
    items: [
      { label: 'Overview', href: '/docs/architecture' },
      { label: 'Runtime Topology', href: '/docs/architecture/runtime-topology' },
      { label: 'Security Tiers', href: '/docs/architecture/security-tiers' },
      { label: 'Execution Modes', href: '/docs/architecture/execution-modes' },
      { label: 'Sandbox Backends', href: '/docs/architecture/sandbox-backends' },
    ],
  },
  // ── The spine: Sealed (cross-cutting; not a pillar) ──────────
  {
    title: 'Security',
    items: [
      { label: 'Sealed — five pillars, one spine', href: '/docs/security' },
    ],
  },
  // ── Pillar 1: SDK ────────────────────────────────────────────
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
      { label: 'VS Code Extension', href: '/docs/guides/vscode-extension' },
      { label: 'Client Wrappers (17 Languages)', href: '/docs/guides/client-wrappers' },
    ],
  },
  // ── Pillar 2: Autopilot ──────────────────────────────────────
  {
    title: 'Autopilot',
    items: [
      { label: 'Overview', href: '/docs/autopilot' },
    ],
  },
  // ── Pillar 3: Fleet (promoted from Deployment) ───────────────
  {
    title: 'Fleet',
    items: [
      { label: 'Fleet Architecture (Enterprise)', href: '/docs/guides/fleet-enterprise' },
      { label: 'Fleet Deployment', href: '/docs/guides/fleet' },
      { label: 'Fleet Deep Dive', href: '/docs/guides/fleet-architecture' },
    ],
  },
  // ── Pillar 4: Observatory ────────────────────────────────────
  {
    title: 'Observatory',
    items: [
      { label: 'Overview', href: '/docs/observatory' },
      { label: 'Admin Panel', href: '/docs/guides/admin-panel' },
      { label: 'Notifications', href: '/docs/api-reference/notifications' },
      { label: 'REST API', href: '/docs/api-reference/rest-api' },
    ],
  },
  // ── Pillar 5: Training Loop ──────────────────────────────────
  {
    title: 'Training Loop',
    items: [
      { label: 'Self-Learning Agents', href: '/docs/core-concepts/self-learning' },
      { label: 'Training & Fine-Tuning', href: '/docs/guides/training' },
      { label: 'Inference — overview', href: '/docs/inference' },
      { label: 'Inference — start with juggernauts', href: '/docs/inference/start-with-juggernauts' },
      { label: 'Inference — free CUDA via Colab', href: '/docs/inference/free-cuda-via-colab' },
      { label: 'Inference — rent when you grow', href: '/docs/inference/rent-when-you-grow' },
      { label: 'Inference — deploy locally', href: '/docs/inference/deploy-locally' },
    ],
  },
  // ── Cross-cutting integration topics ─────────────────────────
  {
    title: 'Tools & MCP',
    items: [
      { label: 'Tools & MCP', href: '/docs/api-reference/tools' },
      { label: 'MCP Protocol', href: '/docs/api-reference/mcp' },
      { label: 'MCP Server', href: '/docs/guides/mcp-server' },
      { label: 'External Access', href: '/docs/guides/external-access' },
      { label: 'Gateway Streaming', href: '/docs/guides/gateway-streaming' },
    ],
  },
  {
    title: 'LLM Proxy',
    items: [
      { label: 'LLM Harness', href: '/docs/guides/harness' },
      { label: 'Cost Management', href: '/docs/guides/cost-management' },
      { label: 'PII Protection', href: '/docs/guides/pii-protection' },
      { label: 'Local Inference', href: '/docs/guides/local-inference' },
      { label: 'CI/CD Integration', href: '/docs/guides/ci-cd' },
    ],
  },
  // ── Deployment & Reference ───────────────────────────────────
  {
    title: 'Deployment',
    items: [
      { label: 'Self-Hosted Deployment', href: '/docs/guides/self-hosted' },
      { label: 'Hardware Requirements', href: '/docs/guides/hardware-requirements' },
      { label: 'Infrastructure Management', href: '/docs/guides/infrastructure' },
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
      { label: 'vs. Alternatives', href: '/docs/guides/vs-alternatives' },
      { label: 'vs. MiniMax', href: '/docs/guides/vs-minimax' },
    ],
  },
];

export function DocsSidebar() {
  const pathname = usePathname();

  return (
    <aside className="w-full h-[calc(100dvh-64px)] lg:w-64 lg:flex-shrink-0 lg:sticky lg:top-16 border-r border-border bg-bg-surface overflow-y-auto">
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
