import type { Metadata } from 'next';
import { CookieConsentProvider } from '@/components/cookie-consent-context';
import { CookieBanner } from '@/components/cookie-banner';
import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL('https://docs.sagewai.ai'),
  title: {
    default: 'Sagewai — The autonomous AI agent platform',
    template: '%s | Sagewai docs',
  },
  description:
    'Open-source agent platform for senior engineers shipping AI under budget: any LLM, MCP tools, typed memory, guardrails, observability, and a training loop that turns Opus runs into a local model you own.',
  keywords: [
    'AI agent platform',
    'LLM agent framework',
    'open source AI agents',
    'Python AI SDK',
    'MCP integration',
    'agent observability',
    'AI cost tracking',
    'fine-tune local LLM',
    'LangChain alternative',
    'LlamaIndex alternative',
    'production AI agents',
    'autonomous agents',
  ],
  authors: [{ name: 'Ali Arda Diri' }],
  creator: 'Sagewai',
  publisher: 'Sagewai',
  alternates: {
    canonical: 'https://docs.sagewai.ai',
  },
  icons: {
    icon: [
      { url: '/brand/favicon.ico', sizes: 'any' },
      { url: '/brand/sagewai_icon.svg', type: 'image/svg+xml' },
    ],
    apple: '/brand/sagewai_icon.webp',
  },
  openGraph: {
    title: 'Sagewai — The autonomous AI agent platform',
    description:
      'Open-source agent platform for senior engineers shipping AI under budget: any LLM, MCP tools, observability, and a training loop that turns Opus runs into a local model you own.',
    url: 'https://docs.sagewai.ai',
    siteName: 'Sagewai',
    images: [
      {
        url: '/brand/sagewai_logo.webp',
        width: 1200,
        height: 630,
        alt: 'Sagewai — the autonomous agent platform',
      },
    ],
    type: 'website',
    locale: 'en_US',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Sagewai — The autonomous AI agent platform',
    description:
      'Build production AI agents with any LLM. Open source, MCP-native, with cost tracking and a training loop that owns the cost-down path.',
    images: ['/brand/sagewai_logo.webp'],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-video-preview': -1,
      'max-image-preview': 'large',
      'max-snippet': -1,
    },
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify({
            '@context': 'https://schema.org',
            '@graph': [
              {
                '@type': 'Organization',
                '@id': 'https://sagewai.ai/#organization',
                name: 'Sagewai',
                url: 'https://sagewai.ai',
                logo: {
                  '@type': 'ImageObject',
                  url: 'https://docs.sagewai.ai/brand/sagewai_logo.webp',
                  width: 512,
                  height: 512,
                },
                description:
                  'Sagewai is the autonomous agent platform for senior engineers shipping AI features under budget. Open-source SDK, hosted Autopilot, Fleet, Observatory, and Training Loop.',
                founder: { '@type': 'Person', name: 'Ali Arda Diri' },
                foundingDate: '2026',
                sameAs: [
                  'https://github.com/sagewai',
                  'https://pypi.org/project/sagewai/',
                ],
              },
              {
                '@type': 'WebSite',
                '@id': 'https://docs.sagewai.ai/#website',
                url: 'https://docs.sagewai.ai',
                name: 'Sagewai Documentation',
                description:
                  'Technical documentation for the Sagewai AI agent platform.',
                inLanguage: 'en-US',
                publisher: { '@id': 'https://sagewai.ai/#organization' },
                potentialAction: {
                  '@type': 'SearchAction',
                  target:
                    'https://docs.sagewai.ai/search?q={search_term_string}',
                  'query-input': 'required name=search_term_string',
                },
              },
              {
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
                license:
                  'https://www.gnu.org/licenses/agpl-3.0.html',
                url: 'https://sagewai.ai',
                downloadUrl: 'https://pypi.org/project/sagewai/',
                author: { '@id': 'https://sagewai.ai/#organization' },
                publisher: { '@id': 'https://sagewai.ai/#organization' },
                featureList: [
                  'Any LLM via LiteLLM (100+ models)',
                  'MCP tool integration',
                  'Typed memory with extraction strategies',
                  'Guardrails (PII, hallucination, content)',
                  'OpenTelemetry observability',
                  'Cost tracking and audit logs',
                  'Distributed Fleet with project isolation',
                  'Autopilot — describe goal in plain English',
                  'Training Loop — capture, fine-tune, deploy local',
                ],
              },
            ],
          }) }}
        />
        <script dangerouslySetInnerHTML={{ __html: `
          (function() {
            var t = localStorage.getItem('sagewai-theme');
            if (!t) t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
            document.documentElement.setAttribute('data-theme', t);
          })();
        ` }} />
      </head>
      <body className="antialiased text-text-primary bg-bg-page overflow-x-hidden">
        <CookieConsentProvider>
          {children}
          <CookieBanner />
        </CookieConsentProvider>
      </body>
    </html>
  );
}
