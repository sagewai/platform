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

// Sidebar order is by INTENT, not by file number.
// See: sagewai/atelier:docs/v1.0/example-catalogue-and-positioning.md
//      (Information architecture section).
//
// Quickstart -> Lighthouse -> Foundation -> Patterns -> Integrations ->
// Pillars -> Sealed spine -> Architecture -> Inference -> Concepts -> Guides -> Reference
//
// This flips the previous chronological-by-file-number listing that
// buried Tier-5 lighthouse work behind 32 entry-level demos.
const SECTIONS: NavSection[] = [
  // -- Tier 1 -- Quickstart -------------------------------------
  {
    title: 'Quickstart',
    items: [
      { label: 'Hello agent in 60 seconds', href: '/docs/quickstart' },
      { label: 'Full quickstart (Claude Code in a sandbox)', href: '/docs/getting-started' },
      { label: 'Minimal setup (no sandbox)', href: '/docs/getting-started/minimal-setup' },
    ],
  },
  // -- Tier 5 -- Lighthouse (the differentiators) ---------------
  {
    title: 'Lighthouse',
    items: [
      { label: 'Overview', href: '/docs/lighthouse' },
      { label: 'Train your own model', href: '/docs/lighthouse/train-your-own-model' },
      { label: 'Moderation and classification', href: '/docs/lighthouse/moderation-and-classification' },
      { label: 'Memory and retrieval', href: '/docs/lighthouse/memory-and-retrieval' },
      { label: 'Production multitenancy', href: '/docs/lighthouse/production-multitenancy' },
      { label: 'Observability and cost', href: '/docs/lighthouse/observability-and-cost' },
      { label: 'Inference deployment', href: '/docs/lighthouse/inference-deployment' },
    ],
  },
  // -- Tier 2 -- Foundation (SDK basics) ------------------------
  {
    title: 'Foundation',
    items: [
      { label: 'Overview', href: '/docs/foundation' },
    ],
  },
  // -- Tier 4 -- Patterns (production reference) ----------------
  {
    title: 'Patterns',
    items: [
      { label: 'Overview', href: '/docs/patterns' },
    ],
  },
  // -- Tier 3 -- Integrations -----------------------------------
  {
    title: 'Integrations',
    items: [
      { label: 'Overview', href: '/docs/integrations' },
    ],
  },
  // -- Pillars (capability deep-dives) --------------------------
  {
    title: 'Pillars',
    items: [
      { label: 'Overview', href: '/docs/pillars' },
      { label: 'SDK', href: '/docs/pillars/sdk' },
      { label: 'Autopilot', href: '/docs/pillars/autopilot' },
      { label: 'Fleet', href: '/docs/pillars/fleet' },
      { label: 'Sealed', href: '/docs/pillars/sealed' },
      { label: 'Observatory', href: '/docs/pillars/observatory' },
      { label: 'Training Loop', href: '/docs/pillars/training-loop' },
    ],
  },
  // -- Sealed spine (cross-cutting security deep dive) ----------
  {
    title: 'Sealed spine',
    items: [
      { label: 'Security overview', href: '/docs/security' },
    ],
  },
  // -- Architecture (canonical contract) ------------------------
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
  // -- Inference education (Gap #9) -----------------------------
  {
    title: 'Inference',
    items: [
      { label: 'Overview', href: '/docs/inference' },
      { label: 'Start with juggernauts', href: '/docs/inference/start-with-juggernauts' },
      { label: 'Free CUDA via Colab', href: '/docs/inference/free-cuda-via-colab' },
      { label: 'Rent when you grow', href: '/docs/inference/rent-when-you-grow' },
      { label: 'Deploy locally', href: '/docs/inference/deploy-locally' },
    ],
  },
  // -- Concept overviews (existing top-level concept pages) -----
  {
    title: 'Concept overviews',
    items: [
      { label: 'Observatory dashboards', href: '/docs/observatory' },
      { label: 'Autopilot overview', href: '/docs/autopilot' },
    ],
  },
  // -- Core concepts --------------------------------------------
  {
    title: 'Core concepts',
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
  // -- Guides ---------------------------------------------------
  {
    title: 'Guides',
    items: [
      { label: 'Your First Agent', href: '/docs/guides/first-agent' },
      { label: 'Tutorials', href: '/docs/guides/tutorials' },
      { label: 'Video Tutorials', href: '/docs/guides/video-tutorials' },
      { label: 'Multi-Agent Workflows', href: '/docs/guides/multi-agent' },
      { label: 'Agent Patterns', href: '/docs/guides/patterns' },
      { label: 'Training & Fine-Tuning', href: '/docs/guides/training' },
      { label: 'Admin Panel', href: '/docs/guides/admin-panel' },
      { label: 'Fleet Architecture', href: '/docs/guides/fleet-enterprise' },
      { label: 'Fleet Deployment', href: '/docs/guides/fleet' },
      { label: 'Fleet Deep Dive', href: '/docs/guides/fleet-architecture' },
      { label: 'Self-Hosted Deployment', href: '/docs/guides/self-hosted' },
      { label: 'Hardware Requirements', href: '/docs/guides/hardware-requirements' },
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
      { label: 'vs. Alternatives', href: '/docs/guides/vs-alternatives' },
      { label: 'vs. MiniMax', href: '/docs/guides/vs-minimax' },
    ],
  },
  // -- Reference (numbered file list, NOT primary discovery) ----
  {
    title: 'Reference',
    items: [
      { label: 'Examples (numbered file list)', href: '/docs/reference/examples' },
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
