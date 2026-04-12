const LEGAL_LINKS = [
  { label: 'Privacy Policy', href: 'https://sagewai.ai/privacy' },
  { label: 'Terms of Service', href: 'https://sagewai.ai/terms' },
  { label: 'Impressum', href: 'https://sagewai.ai/impressum' },
  { label: 'Cookie Preferences', href: 'https://sagewai.ai/cookies' },
];

export function DocsFooter() {
  return (
    <footer className="border-t border-border-dim mt-12">
      <div className="max-w-[90rem] mx-auto px-4 sm:px-6 py-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
        <p className="text-xs text-text-muted">
          © 2026 Sagewai. All rights reserved.
        </p>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          {LEGAL_LINKS.map(({ label, href }) => (
            <a
              key={label}
              href={href}
              className="text-xs text-text-secondary hover:text-text-primary transition-colors"
            >
              {label}
            </a>
          ))}
        </div>
      </div>
    </footer>
  );
}
