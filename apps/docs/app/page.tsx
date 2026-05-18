import Link from 'next/link';
import {
  Code2,
  Database,
  Shield,
  BarChart3,
  GraduationCap,
  Lock,
} from 'lucide-react';
import { FeatureCard } from '@/components/feature-card';
import { CodeBlock } from '@/components/code-block';
import { ModelBadge } from '@/components/model-badge';
import { ThemeToggle } from '@/components/theme-toggle';

const FEATURES = [
  {
    icon: <Code2 size={28} />,
    title: 'SDK',
    description:
      'Python-native agent runtime — multi-model providers, tools via MCP gateway, typed memory with extraction strategies and per-mission branching and checkpoint save/restore, guardrails, and LLM proxy in one import. Three lines to your first agent, 100+ models out of the box.',
  },
  {
    icon: <Database size={28} />,
    title: 'Autopilot',
    description:
      'State the goal in plain English. Autopilot designs the agent graph, extracts the slots, previews the plan, runs the mission, and heals on failure.',
  },
  {
    icon: <Shield size={28} />,
    title: 'Fleet',
    description:
      'Distributed workers with capability-based dispatch, project isolation, enrollment keys, and isolated execution sandboxes (image families, Kubernetes backend, AgentCore-runtime backend, pooling). Run agents on your hardware, in your network.',
  },
  {
    icon: <BarChart3 size={28} />,
    title: 'Observatory',
    description:
      'OpenTelemetry tracing, VictoriaMetrics metrics, Grafana dashboards, cost tracking, audit trail. Your AI source of truth — answer "what did AI cost us this month?" in one click.',
  },
  {
    icon: <GraduationCap size={28} />,
    title: 'Training Loop',
    description:
      'Curate production runs, export for Unsloth, fine-tune local models, promote the good ones. Agents that get cheaper with use — $0 per token at the limit.',
  },
];

const SPINE = {
  icon: <Lock size={28} />,
  title: 'Sealed — the security layer',
  description:
    'Per-CLI workload identity, externalised secret backends with just-in-time credentials, redaction at the RPC boundary, per-CLI access control, JIT human-in-the-loop on high-privilege actions, and replay-safe audit. Sealed is the security layer wired into every part of the platform.',
};

const MODELS = [
  { name: 'GPT-4o', provider: 'OpenAI' },
  { name: 'GPT-4o-mini', provider: 'OpenAI' },
  { name: 'Claude 3.5 Sonnet', provider: 'Anthropic' },
  { name: 'Claude Opus 4', provider: 'Anthropic' },
  { name: 'Gemini 2.0 Flash', provider: 'Google' },
  { name: 'Gemini 2.5 Pro', provider: 'Google' },
  { name: 'Mistral Large', provider: 'Mistral' },
  { name: 'Command R+', provider: 'Cohere' },
  { name: 'DeepSeek V3', provider: 'DeepSeek' },
  { name: 'Llama 3.1 405B', provider: 'Meta' },
];

const QUICKSTART_CODE = `from sagewai import UniversalAgent, tool

@tool
async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"Sunny, 22\u00B0C in {city}"

agent = UniversalAgent(
    name="weather-bot",
    model="gpt-4o",
    tools=[get_weather],
)

response = await agent.chat("What's the weather in Berlin?")
print(response)  # "It's sunny and 22\u00B0C in Berlin!"`;

const WORKFLOW_CODE = `from sagewai import UniversalAgent, SequentialAgent, ParallelAgent

researcher = UniversalAgent(name="researcher", model="gpt-4o")
writer = UniversalAgent(name="writer", model="claude-3-5-sonnet-20241022")
reviewer = UniversalAgent(name="reviewer", model="gpt-4o-mini")

# Pipeline: research -> write -> review
pipeline = SequentialAgent(
    name="article-pipeline",
    agents=[researcher, writer, reviewer],
)

result = await pipeline.chat("Write about quantum computing")`;

const GUARDRAILS_CODE = `from sagewai import UniversalAgent
from sagewai.safety.pii import PIIGuard, PIIEntityType
from sagewai.safety.hallucination import HallucinationGuard

agent = UniversalAgent(
    name="safe-agent",
    model="gpt-4o",
    guardrails=[
        PIIGuard(action="redact", entity_types=[
            PIIEntityType.EMAIL,
            PIIEntityType.PHONE,
            PIIEntityType.SSN,
        ]),
        HallucinationGuard(threshold=0.3, action="warn"),
    ],
)

# PII is automatically redacted before reaching the LLM
# Hallucinations are flagged based on RAG context grounding`;

export default function LandingPage() {
  return (
    <div className="min-h-screen">
      {/* Navigation — solid background at all times (see the navbar rule in
          sagewai/atelier:brand/docs/sagewai_branding.md § "Logo & Navbar"). */}
      <nav className="sticky top-0 z-50 bg-bg-page border-b border-border">
        <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2 shrink-0 min-w-0">
            {/* Full wordmark logo on all breakpoints. Light/dark variants. */}
            <img
              src="/brand/sagewai_logo.svg"
              alt="Sagewai"
              className="h-8 w-auto block dark:hidden"
            />
            <img
              src="/brand/sagewai_logo_dark.svg"
              alt="Sagewai"
              className="h-8 w-auto hidden dark:block"
            />
            <span className="text-xs bg-primary-light text-primary px-2 py-0.5 rounded-full font-medium shrink-0">
              SDK
            </span>
          </Link>
          <div className="hidden md:flex items-center gap-8">
            <Link href="/docs/get-started/quickstart" className="text-sm text-text-secondary hover:text-primary transition-colors">
              Docs
            </Link>
            <Link href="/docs/api-reference/python-sdk" className="text-sm text-text-secondary hover:text-primary transition-colors">
              API Reference
            </Link>
            <Link href="/docs/get-started/first-agent" className="text-sm text-text-secondary hover:text-primary transition-colors">
              Guides
            </Link>
            <a
              href="https://github.com/sagewai/platform"
              className="text-sm text-text-secondary hover:text-primary transition-colors"
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
            <ThemeToggle />
          </div>
          <Link
            href="/docs/get-started/quickstart"
            className="bg-primary text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-primary-hover transition-colors"
          >
            Get Started
          </Link>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 bg-gradient-to-b from-primary-light/80 to-bg-page pointer-events-none" />
        <div className="relative max-w-6xl mx-auto px-6 pt-24 pb-16">
          <div className="max-w-3xl mx-auto text-center">
            <div className="inline-flex items-center gap-2 bg-primary-light text-primary text-sm px-4 py-1.5 rounded-full mb-6 font-medium">
              Open Source · The Autonomous Agent Platform
            </div>
            <h1 className="text-5xl md:text-6xl font-bold text-text-primary leading-tight tracking-tight mb-6">
              Build agents that{' '}
              <span className="bg-gradient-to-r from-primary to-accent-purple bg-clip-text text-transparent">
                run in production.
              </span>
            </h1>
            <p className="text-xl text-text-secondary leading-relaxed mb-10 max-w-[42rem] mx-auto">
              Sagewai is an open-source agent platform: describe the goal, the Autopilot designs
              the agent graph, workers run it in isolation, and the Training Loop fine-tunes local
              models so every run gets cheaper.
            </p>
            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <Link
                href="/docs/get-started/quickstart"
                className="w-full sm:w-auto bg-primary text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-primary-hover transition-colors shadow-lg shadow-primary/20"
              >
                Get Started
              </Link>
              <a
                href="https://github.com/sagewai/platform"
                className="w-full sm:w-auto border border-border text-text-primary px-8 py-3.5 rounded-xl text-base font-semibold hover:border-primary hover:bg-bg-subtle transition-colors"
                target="_blank"
                rel="noopener noreferrer"
              >
                View on GitHub
              </a>
            </div>
          </div>
        </div>
      </section>

      {/* Quick Code Example */}
      <section className="max-w-4xl mx-auto px-6 pb-16">
        <CodeBlock code={QUICKSTART_CODE} title="quickstart.py" />
      </section>

      {/* Feature Cards */}
      <section className="max-w-6xl mx-auto px-6 py-20">
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-text-primary mb-3">What you get</h2>
          <p className="text-text-secondary max-w-[42rem] mx-auto italic">
            The platform is the SDK plus four capabilities — Autopilot, Fleet, Observatory, Training Loop — with Sealed security across all of them.
          </p>
        </div>
        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
          {FEATURES.map((f) => (
            <FeatureCard key={f.title} icon={f.icon} title={f.title} description={f.description} />
          ))}
        </div>
      </section>

      {/* Sealed security card — visually distinct from the capability grid */}
      <section className="max-w-4xl mx-auto px-6 pb-20">
        <FeatureCard
          icon={SPINE.icon}
          title={SPINE.title}
          description={SPINE.description}
        />
      </section>

      {/* Code Examples Section */}
      <section className="bg-bg-subtle border-y border-border py-20">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-12">
            <h2 className="text-3xl font-bold text-text-primary mb-3">Built for Real Workloads</h2>
            <p className="text-text-secondary max-w-[42rem] mx-auto">
              From simple single-agent tasks to complex multi-agent pipelines with safety
              guardrails and cost controls.
            </p>
          </div>
          <div className="grid lg:grid-cols-2 gap-8">
            <div className="min-w-0">
              <h3 className="text-lg font-semibold text-text-primary mb-2">Multi-Agent Workflows</h3>
              <p className="text-sm text-text-secondary mb-4">
                Compose agents into sequential, parallel, or loop patterns. Each agent can use a
                different model.
              </p>
              <CodeBlock code={WORKFLOW_CODE} title="workflow.py" />
            </div>
            <div className="min-w-0">
              <h3 className="text-lg font-semibold text-text-primary mb-2">Safety Guardrails</h3>
              <p className="text-sm text-text-secondary mb-4">
                Protect inputs and outputs with PII detection, hallucination guards, content
                filters, and token budgets.
              </p>
              <CodeBlock code={GUARDRAILS_CODE} title="guardrails.py" />
            </div>
          </div>
        </div>
      </section>

      {/* Model Compatibility */}
      <section className="max-w-6xl mx-auto px-6 py-20">
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold text-text-primary mb-3">100+ Supported Models</h2>
          <p className="text-text-secondary max-w-[42rem] mx-auto">
            Write your agent once, then swap models with a single parameter.
            No code changes required.
          </p>
        </div>
        <div className="flex flex-wrap justify-center gap-3">
          {MODELS.map((m) => (
            <ModelBadge key={m.name} name={m.name} provider={m.provider} />
          ))}
        </div>
        <p className="text-center text-sm text-text-muted mt-6">
          Plus Azure OpenAI, AWS Bedrock, Vertex AI, Together AI, Groq, Fireworks, and many more.
        </p>
      </section>

      {/* Architecture Overview */}
      <section className="bg-bg-subtle border-y border-border py-20">
        <div className="max-w-6xl mx-auto px-6">
          <div className="text-center mb-12">
            <h2 className="text-3xl font-bold text-text-primary mb-3">Modular Architecture</h2>
            <p className="text-text-secondary max-w-[42rem] mx-auto">
              Use what you need. Every module is independently importable and composable.
            </p>
          </div>
          <div className="grid md:grid-cols-2 lg:grid-cols-5 gap-4">
            {[
              {
                label: 'SDK',
                items: ['BaseAgent', 'Strategies', 'Workflows', 'Memory & RAG', 'Guardrails'],
              },
              {
                label: 'Autopilot',
                items: ['Goal Router', 'Mission Driver', 'Slot Extraction', 'Plan Preview', 'Self-Healing'],
              },
              {
                label: 'Fleet',
                items: ['Workers', 'Sandbox Backends', 'Capability Dispatch', 'Project Isolation', 'Image Families'],
              },
              {
                label: 'Observatory',
                items: ['Cost Tracking', 'Audit Logs', 'Prometheus Metrics', 'OpenTelemetry'],
              },
              {
                label: 'Training Loop',
                items: ['Curator', 'Unsloth Integration', 'Promoter', 'Local LLM Discovery', 'Fine-Tuning Pipeline'],
              },
            ].map((col) => (
              <div key={col.label} className="bg-bg-page rounded-xl border border-border p-5">
                <h3 className="font-semibold text-primary mb-3 text-sm uppercase tracking-wide">
                  {col.label}
                </h3>
                <ul className="space-y-2">
                  {col.items.map((item) => (
                    <li key={item} className="text-sm text-text-secondary flex items-center gap-2">
                      <span className="w-1.5 h-1.5 rounded-full bg-primary flex-shrink-0" />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="max-w-6xl mx-auto px-6 py-24 text-center">
        <h2 className="text-3xl font-bold text-text-primary mb-4">Ready to build?</h2>
        <p className="text-text-secondary mb-8 max-w-[36rem] mx-auto">
          Install the SDK and create your first agent in under a minute.
        </p>
        <div className="bg-bg-deep rounded-xl inline-block px-8 py-4 mb-8">
          <code className="text-primary text-lg font-mono">pip install sagewai</code>
        </div>
        <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
          <Link
            href="/docs/get-started/quickstart"
            className="bg-primary text-white px-8 py-3.5 rounded-xl text-base font-semibold hover:bg-primary-hover transition-colors shadow-lg shadow-primary/20"
          >
            Read the Docs
          </Link>
          <Link
            href="/docs/get-started/first-agent"
            className="border border-border text-text-primary px-8 py-3.5 rounded-xl text-base font-semibold hover:border-primary hover:bg-bg-subtle transition-colors"
          >
            First Agent Tutorial
          </Link>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-border bg-bg-subtle py-12">
        <div className="max-w-6xl mx-auto px-6">
          <div className="grid md:grid-cols-4 gap-8">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <img
                  src="/brand/sagewai_logo.svg"
                  alt="Sagewai"
                  className="h-6 w-auto block dark:hidden"
                />
                <img
                  src="/brand/sagewai_logo_dark.svg"
                  alt="Sagewai"
                  className="h-6 w-auto hidden dark:block"
                />
              </div>
              <p className="text-sm text-text-muted mt-2">
                The autonomous agent platform.
              </p>
            </div>
            <div>
              <h4 className="font-semibold text-text-primary mb-3 text-sm">Documentation</h4>
              <ul className="space-y-2">
                <li>
                  <Link href="/docs/get-started/quickstart" className="text-sm text-text-secondary hover:text-primary">
                    Getting Started
                  </Link>
                </li>
                <li>
                  <Link href="/docs/core-concepts/agents" className="text-sm text-text-secondary hover:text-primary">
                    Core Concepts
                  </Link>
                </li>
                <li>
                  <Link href="/docs/api-reference/python-sdk" className="text-sm text-text-secondary hover:text-primary">
                    API Reference
                  </Link>
                </li>
              </ul>
            </div>
            <div>
              <h4 className="font-semibold text-text-primary mb-3 text-sm">Guides</h4>
              <ul className="space-y-2">
                <li>
                  <Link href="/docs/tutorials" className="text-sm text-text-secondary hover:text-primary">
                    Tutorials
                  </Link>
                </li>
                <li>
                  <Link href="/docs/guides/fleet-architecture" className="text-sm text-text-secondary hover:text-primary">
                    Fleet Architecture
                  </Link>
                </li>
                <li>
                  <Link href="/docs/guides/client-wrappers" className="text-sm text-text-secondary hover:text-primary">
                    Client Wrappers
                  </Link>
                </li>
                <li>
                  <Link href="/docs/guides/cost-management" className="text-sm text-text-secondary hover:text-primary">
                    Cost Management
                  </Link>
                </li>
              </ul>
            </div>
            <div>
              <h4 className="font-semibold text-text-primary mb-3 text-sm">Community</h4>
              <ul className="space-y-2">
                <li>
                  <a
                    href="https://github.com/sagewai/platform"
                    className="text-sm text-text-secondary hover:text-primary"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    GitHub
                  </a>
                </li>
                <li>
                  <a
                    href="https://github.com/sagewai/platform/issues"
                    className="text-sm text-text-secondary hover:text-primary"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Issues
                  </a>
                </li>
              </ul>
            </div>
          </div>
          <div className="border-t border-border mt-8 pt-8 flex flex-col sm:flex-row items-center justify-between gap-3 text-sm text-text-muted">
            <span>© 2026 Sagewai. All rights reserved.</span>
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
              <a href="https://sagewai.ai/privacy" className="hover:text-text-primary transition-colors">Privacy Policy</a>
              <a href="https://sagewai.ai/terms" className="hover:text-text-primary transition-colors">Terms</a>
              <a href="https://sagewai.ai/impressum" className="hover:text-text-primary transition-colors">Impressum</a>
              <a href="https://sagewai.ai/cookies" className="hover:text-text-primary transition-colors">Cookies</a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
