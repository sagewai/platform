import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Sagewai Documentation',
  description:
    'Agent infrastructure you own. Build production-grade AI agents with any model.',
  icons: { icon: '/brand/favicon.ico' },
  openGraph: {
    title: 'Sagewai Documentation',
    description: 'Agent infrastructure you own. Build production-grade AI agents with any model.',
    url: 'https://docs.sagewai.ai',
    siteName: 'Sagewai',
    images: [{ url: '/brand/logo-512.png', width: 512, height: 512 }],
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
      <body className="antialiased text-text-primary bg-bg-page">{children}</body>
    </html>
  );
}
