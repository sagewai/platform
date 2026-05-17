/**
 * Schema.org JSON-LD components for SEO + AI-engine readability.
 *
 * Embedded inline with <Script type="application/ld+json"> so AI
 * crawlers (Perplexity, ChatGPT-search, Claude search) parse them
 * without executing JS.
 *
 * Usage in MDX:
 *
 *   import { HowToJsonLd, TechArticleJsonLd } from '@/components/structured-data';
 *
 *   <HowToJsonLd
 *     name="..."
 *     description="..."
 *     steps={[{ name: '...', text: '...' }]}
 *   />
 */
import type { ReactElement } from 'react';

const SITE_URL = 'https://docs.sagewai.ai';
const ORG_ID = 'https://sagewai.ai/#organization';

/**
 * SoftwareApplication markup for the Sagewai SDK. Embed once per
 * lighthouse-style page so AI engines link the page to the SDK.
 */
export function SoftwareApplicationJsonLd(): ReactElement {
  const data = {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    '@id': 'https://sagewai.ai/#sdk',
    name: 'Sagewai SDK',
    applicationCategory: 'DeveloperApplication',
    applicationSubCategory: 'AI Agent Framework',
    operatingSystem: 'Linux, macOS, Windows',
    description:
      'Open-source Python SDK for building production-grade AI agents with any LLM. Multi-model providers, MCP tool integration, typed memory, guardrails, and built-in observability.',
    offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
    softwareVersion: '1.0',
    license: 'https://www.gnu.org/licenses/agpl-3.0.html',
    url: 'https://sagewai.ai',
    downloadUrl: 'https://pypi.org/project/sagewai/',
    author: { '@id': ORG_ID },
    publisher: { '@id': ORG_ID },
    aggregateRating: undefined,
    keywords:
      'AI agent platform, LLM agent framework, Python AI SDK, MCP integration, agent observability, AI cost tracking, fine-tune local LLM, open source agent framework',
  };
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data) }}
    />
  );
}

/**
 * Organization markup for Sagewai. Already present in root layout
 * indirectly; this component is the canonical site-wide form.
 */
export function OrganizationJsonLd(): ReactElement {
  const data = {
    '@context': 'https://schema.org',
    '@type': 'Organization',
    '@id': ORG_ID,
    name: 'Sagewai',
    url: 'https://sagewai.ai',
    logo: 'https://docs.sagewai.ai/brand/sagewai_logo.webp',
    description:
      'Sagewai is the autonomous agent platform for senior engineers shipping AI features under budget. Open-source SDK, hosted Autopilot, Fleet, Observatory, and Training Loop.',
    sameAs: [
      'https://github.com/sagewai',
      'https://pypi.org/project/sagewai/',
    ],
  };
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data) }}
    />
  );
}

export interface HowToStep {
  name: string;
  text: string;
  url?: string;
}

export interface HowToProps {
  /** Page title — short and use-case-led. */
  name: string;
  /** Page description — under 160 chars. */
  description: string;
  /** Canonical URL of the page (path under docs.sagewai.ai). */
  path: string;
  /** Numbered procedural steps. AI engines extract these directly. */
  steps: HowToStep[];
  /** ISO date the article was first published. */
  datePublished?: string;
  /** Optional cost/time-required hints. */
  totalTime?: string;
  estimatedCost?: { currency: string; value: string };
}

/**
 * Combined HowTo + TechArticle markup for Tier-5 lighthouse pages.
 *
 * AI engines parse `step` arrays for procedural recall ("how do I
 * build X with Sagewai") and `headline` for relevance ranking.
 */
export function HowToJsonLd(props: HowToProps): ReactElement {
  const {
    name,
    description,
    path,
    steps,
    datePublished = '2026-05-29',
    totalTime,
    estimatedCost,
  } = props;
  const url = `${SITE_URL}${path}`;
  const data = {
    '@context': 'https://schema.org',
    '@type': ['TechArticle', 'HowTo'],
    headline: name,
    name,
    description,
    url,
    mainEntityOfPage: { '@type': 'WebPage', '@id': url },
    datePublished,
    dateModified: datePublished,
    inLanguage: 'en-US',
    author: { '@id': ORG_ID },
    publisher: { '@id': ORG_ID },
    image: `${SITE_URL}/brand/sagewai_logo.webp`,
    ...(totalTime ? { totalTime } : {}),
    ...(estimatedCost
      ? {
          estimatedCost: {
            '@type': 'MonetaryAmount',
            currency: estimatedCost.currency,
            value: estimatedCost.value,
          },
        }
      : {}),
    tool: [{ '@type': 'SoftwareApplication', name: 'Sagewai SDK' }],
    step: steps.map((s, i) => ({
      '@type': 'HowToStep',
      position: i + 1,
      name: s.name,
      text: s.text,
      ...(s.url ? { url: s.url } : {}),
    })),
  };
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data) }}
    />
  );
}

export interface TechArticleProps {
  name: string;
  description: string;
  path: string;
  datePublished?: string;
  /** Article section, e.g. "Architecture", "Inference". */
  articleSection?: string;
}

/**
 * TechArticle markup for non-procedural lighthouse pages
 * (architecture, security, observatory).
 */
export function TechArticleJsonLd(props: TechArticleProps): ReactElement {
  const {
    name,
    description,
    path,
    datePublished = '2026-05-29',
    articleSection,
  } = props;
  const url = `${SITE_URL}${path}`;
  const data = {
    '@context': 'https://schema.org',
    '@type': 'TechArticle',
    headline: name,
    name,
    description,
    url,
    mainEntityOfPage: { '@type': 'WebPage', '@id': url },
    datePublished,
    dateModified: datePublished,
    inLanguage: 'en-US',
    author: { '@id': ORG_ID },
    publisher: { '@id': ORG_ID },
    image: `${SITE_URL}/brand/sagewai_logo.webp`,
    ...(articleSection ? { articleSection } : {}),
    about: { '@id': 'https://sagewai.ai/#sdk' },
  };
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data) }}
    />
  );
}
