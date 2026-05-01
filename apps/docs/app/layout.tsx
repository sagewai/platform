import type { Metadata } from 'next';
import { CookieConsentProvider } from '@/components/cookie-consent-context';
import { CookieBanner } from '@/components/cookie-banner';
import './globals.css';

export const metadata: Metadata = {
  metadataBase: new URL('https://docs.sagewai.ai'),
  title: 'Sagewai Documentation',
  description:
    'Sagewai is the autonomous agent platform: describe the goal, we design the agents, run them in production, and fine-tune local models so every run gets cheaper.',
  icons: {
    icon: [
      { url: '/brand/favicon.ico', sizes: 'any' },
      { url: '/brand/sagewai_icon.svg', type: 'image/svg+xml' },
    ],
    apple: '/brand/sagewai_icon.webp',
  },
  openGraph: {
    title: 'Sagewai Documentation',
    description: 'Sagewai is the autonomous agent platform: describe the goal, we design the agents, run them in production, and fine-tune local models so every run gets cheaper.',
    url: 'https://docs.sagewai.ai',
    siteName: 'Sagewai',
    images: [{ url: '/brand/sagewai_logo.webp' }],
    type: 'website',
  },
  twitter: {
    card: 'summary',
    title: 'Sagewai Documentation',
    description: 'The factory that runs itself.',
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
                '@type': 'WebSite',
                '@id': 'https://docs.sagewai.ai/#website',
                url: 'https://docs.sagewai.ai',
                name: 'Sagewai Documentation',
                description: 'Technical documentation for the Sagewai AI agent infrastructure platform.',
                publisher: { '@id': 'https://sagewai.ai/#organization' },
              },
              {
                '@type': 'TechArticle',
                headline: 'Sagewai Documentation',
                description: 'Build production-grade AI agents with any model.',
                url: 'https://docs.sagewai.ai',
                publisher: { '@id': 'https://sagewai.ai/#organization' },
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
