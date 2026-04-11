import Link from 'next/link';
import { DocsSidebar } from '@/components/docs-sidebar';
import { ThemeToggle } from '@/components/theme-toggle';

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-bg-page">
      {/* Docs Navigation Bar */}
      <nav className="sticky top-0 z-50 bg-bg-page/80 backdrop-blur-md border-b border-border">
        <div className="max-w-[90rem] mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link href="/" className="flex items-center gap-2">
              {/* Full logo (includes wordmark) — light/dark variants. */}
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
              <span className="text-xs bg-primary-light text-primary px-2 py-0.5 rounded-full font-medium">
                Docs
              </span>
            </Link>
          </div>
          <div className="hidden md:flex items-center gap-8">
            <Link
              href="/docs/getting-started"
              className="text-sm text-text-secondary hover:text-primary transition-colors"
            >
              Getting Started
            </Link>
            <Link
              href="/docs/api-reference/python-sdk"
              className="text-sm text-text-secondary hover:text-primary transition-colors"
            >
              API Reference
            </Link>
            <Link
              href="/docs/guides/first-agent"
              className="text-sm text-text-secondary hover:text-primary transition-colors"
            >
              Guides
            </Link>
            <a
              href="https://github.com/sagewai/sagewai"
              className="text-sm text-text-secondary hover:text-primary transition-colors"
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
            <ThemeToggle />
          </div>
        </div>
      </nav>

      <div className="flex max-w-[90rem] mx-auto">
        <DocsSidebar />
        <main className="flex-1 min-w-0 px-8 py-8 lg:px-16 lg:py-12">
          <article className="max-w-3xl">{children}</article>
        </main>
      </div>
    </div>
  );
}
