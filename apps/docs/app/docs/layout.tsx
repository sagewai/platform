import Link from 'next/link';
import { DocsSidebar } from '@/components/docs-sidebar';

export default function DocsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-white">
      {/* Docs Navigation Bar */}
      <nav className="sticky top-0 z-50 bg-white/80 backdrop-blur-md border-b border-gray-200">
        <div className="max-w-[90rem] mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link href="/" className="flex items-center gap-2">
              <span className="text-xl font-bold text-emerald-700">Sagewai</span>
              <span className="text-xs bg-emerald-100 text-emerald-700 px-2 py-0.5 rounded-full font-medium">
                Docs
              </span>
            </Link>
          </div>
          <div className="hidden md:flex items-center gap-8">
            <Link
              href="/docs/getting-started"
              className="text-sm text-gray-600 hover:text-emerald-700 transition-colors"
            >
              Getting Started
            </Link>
            <Link
              href="/docs/api-reference/python-sdk"
              className="text-sm text-gray-600 hover:text-emerald-700 transition-colors"
            >
              API Reference
            </Link>
            <Link
              href="/docs/guides/first-agent"
              className="text-sm text-gray-600 hover:text-emerald-700 transition-colors"
            >
              Guides
            </Link>
            <a
              href="https://github.com/sagewai/sagewai"
              className="text-sm text-gray-600 hover:text-emerald-700 transition-colors"
              target="_blank"
              rel="noopener noreferrer"
            >
              GitHub
            </a>
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
