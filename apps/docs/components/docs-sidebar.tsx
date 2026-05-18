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

// Sidebar order follows the developer journey: Introduction -> Get Started ->
// Core Concepts -> Platform -> Guides -> Inference -> Tutorials ->
// Architecture -> Reference.
const SECTIONS: NavSection[] = [
  {
    title: 'Introduction',
    items: [{ label: 'Overview', href: '/docs' }],
  },
  {
    title: 'Get Started',
    items: [
      { label: 'Installation', href: '/docs/get-started/installation' },
      { label: 'Prerequisites', href: '/docs/get-started/prerequisites' },
      { label: 'Your first agent', href: '/docs/get-started/first-agent' },
      { label: 'Full quickstart', href: '/docs/get-started/quickstart' },
      { label: 'Minimal setup', href: '/docs/get-started/minimal-setup' },
    ],
  },
  {
    title: 'Core Concepts',
    items: [
      { label: 'Agents', href: '/docs/core-concepts/agents' },
      { label: 'Strategies', href: '/docs/core-concepts/strategies' },
      { label: 'Memory & RAG', href: '/docs/core-concepts/memory' },
      { label: 'Workflows', href: '/docs/core-concepts/workflows' },
      { label: 'Context Engine', href: '/docs/core-concepts/context-engine' },
      { label: 'Directives', href: '/docs/core-concepts/directives' },
      { label: 'Safety & Guardrails', href: '/docs/core-concepts/safety' },
      { label: 'Self-Learning Agents', href: '/docs/core-concepts/self-learning' },
    ],
  },
  {
    title: 'Platform',
    items: [
      { label: 'Overview', href: '/docs/platform' },
      { label: 'SDK', href: '/docs/platform/sdk' },
      { label: 'Autopilot', href: '/docs/platform/autopilot' },
      { label: 'Fleet', href: '/docs/platform/fleet' },
      { label: 'Observatory', href: '/docs/platform/observatory' },
      { label: 'Training Loop', href: '/docs/platform/training-loop' },
      { label: 'Security', href: '/docs/platform/security' },
    ],
  },
  {
    title: 'Guides',
    items: [
      { label: 'Multi-Agent Workflows', href: '/docs/guides/multi-agent' },
      { label: 'Training & Fine-Tuning', href: '/docs/guides/training' },
      { label: 'Admin Panel', href: '/docs/guides/admin-panel' },
      { label: 'Fleet Deployment', href: '/docs/guides/fleet-deployment' },
      { label: 'Fleet Architecture', href: '/docs/guides/fleet-architecture' },
      { label: 'Self-Hosted Deployment', href: '/docs/guides/self-hosted' },
      { label: 'Infrastructure Management', href: '/docs/guides/infrastructure' },
      { label: 'MCP Server', href: '/docs/guides/mcp-server' },
      { label: 'External Access', href: '/docs/guides/external-access' },
      { label: 'Gateway Streaming', href: '/docs/guides/gateway-streaming' },
      { label: 'LLM Harness', href: '/docs/guides/harness' },
      { label: 'Cost Management', href: '/docs/guides/cost-management' },
      { label: 'PII Protection', href: '/docs/guides/pii-protection' },
      { label: 'Local Inference', href: '/docs/guides/local-inference' },
      { label: 'Small and Local Models', href: '/docs/guides/small-models' },
      { label: 'CI/CD Integration', href: '/docs/guides/ci-cd' },
      { label: 'VS Code Extension', href: '/docs/guides/vscode-extension' },
      { label: 'Client Wrappers (17 Languages)', href: '/docs/guides/client-wrappers' },
      { label: 'Integrations', href: '/docs/guides/integrations' },
      { label: 'Video Tutorials', href: '/docs/guides/video-tutorials' },
      { label: 'vs. Alternatives', href: '/docs/guides/vs-alternatives' },
      { label: 'vs. MiniMax', href: '/docs/guides/vs-minimax' },
    ],
  },
  {
    title: 'Inference',
    items: [
      { label: 'Overview', href: '/docs/inference' },
      { label: 'Start with the big providers', href: '/docs/inference/start-with-juggernauts' },
      { label: 'Free CUDA via Colab', href: '/docs/inference/free-cuda-via-colab' },
      { label: 'Rent when you grow', href: '/docs/inference/rent-when-you-grow' },
      { label: 'Deploy locally', href: '/docs/inference/deploy-locally' },
    ],
  },
  {
    title: 'Tutorials',
    items: [
      { label: 'Overview', href: '/docs/tutorials' },
      { label: 'Learn the SDK step by step', href: '/docs/tutorials/learn-the-sdk' },
      { label: 'Train your own model', href: '/docs/tutorials/train-your-own-model' },
      { label: 'Moderation and classification', href: '/docs/tutorials/moderation-and-classification' },
      { label: 'Memory and retrieval', href: '/docs/tutorials/memory-and-retrieval' },
      { label: 'Production multitenancy', href: '/docs/tutorials/production-multitenancy' },
      { label: 'Observability and cost', href: '/docs/tutorials/observability-and-cost' },
      { label: 'Inference deployment', href: '/docs/tutorials/inference-deployment' },
      { label: 'Production patterns', href: '/docs/tutorials/production-patterns' },
    ],
  },
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
  {
    title: 'Reference',
    items: [
      { label: 'Examples (numbered file list)', href: '/docs/reference/examples' },
      { label: 'API — Python SDK', href: '/docs/api-reference/python-sdk' },
      { label: 'API — Agents', href: '/docs/api-reference/agents' },
      { label: 'API — Strategies', href: '/docs/api-reference/strategies' },
      { label: 'API — Memory & Context', href: '/docs/api-reference/memory' },
      { label: 'API — Workflows', href: '/docs/api-reference/workflows' },
      { label: 'API — Safety & Guardrails', href: '/docs/api-reference/safety' },
      { label: 'API — Directives', href: '/docs/api-reference/directives' },
      { label: 'API — Self-Learning', href: '/docs/api-reference/self-learning' },
      { label: 'API — Project & Errors', href: '/docs/api-reference/project' },
      { label: 'API — Tools & MCP', href: '/docs/api-reference/tools' },
      { label: 'API — MCP Protocol', href: '/docs/api-reference/mcp' },
      { label: 'API — Notifications', href: '/docs/api-reference/notifications' },
      { label: 'API — REST', href: '/docs/api-reference/rest-api' },
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
