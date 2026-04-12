import type { Metadata } from 'next';
import { CookieConsentProvider } from '@/components/cookie-consent-context';
import { CookieBanner } from '@/components/cookie-banner';
import './globals.css';

export const metadata: Metadata = {
  title: 'Sagewai Documentation',
  description:
    'Agent infrastructure you own. Build production-grade AI agents with any model.',
  icons: {
    icon: [
      { url: '/brand/favicon.ico', sizes: 'any' },
      { url: '/brand/sagewai_icon.svg', type: 'image/svg+xml' },
    ],
    apple: '/brand/sagewai_icon.webp',
  },
  openGraph: {
    title: 'Sagewai Documentation',
    description: 'Agent infrastructure you own. Build production-grade AI agents with any model.',
    url: 'https://docs.sagewai.ai',
    siteName: 'Sagewai',
    images: [{ url: '/brand/sagewai_logo.webp' }],
    type: 'website',
  },
  twitter: {
    card: 'summary',
    title: 'Sagewai Documentation',
    description: 'Agent infrastructure you own.',
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* TODO: Replace with <Script strategy="beforeInteractive"> + CSP nonce when adding Content-Security-Policy headers */}
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
