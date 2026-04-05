import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Sagewai Documentation',
  description:
    'Sagewai — The LLM-agnostic agent framework for building enterprise AI applications.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
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
