'use client';

import { useState } from 'react';
import { useCookieConsent } from './cookie-consent-context';

const CATEGORIES = [
  {
    id: 'essential' as const,
    label: 'Essential',
    description: 'Required for the website to function. Cannot be disabled.',
    locked: true,
  },
  {
    id: 'analytics' as const,
    label: 'Analytics',
    description: 'Help us understand how visitors interact with our website.',
    locked: false,
  },
  {
    id: 'marketing' as const,
    label: 'Marketing',
    description: 'Used to deliver relevant advertisements and track campaign performance.',
    locked: false,
  },
  {
    id: 'preferences' as const,
    label: 'Preferences',
    description: 'Remember your settings and personalisation choices.',
    locked: false,
  },
] as const;

export function CookieBanner() {
  const { hasConsented, loaded, acceptAll, rejectAll, updateConsent } = useCookieConsent();
  const [showCustomise, setShowCustomise] = useState(false);
  const [draft, setDraft] = useState({
    analytics: false,
    marketing: false,
    preferences: false,
  });

  if (!loaded || hasConsented) return null;

  return (
    <div
      role="dialog"
      aria-label="Cookie consent"
      className="fixed bottom-0 left-0 right-0 z-[60] border-t border-border bg-bg-surface/95 backdrop-blur-lg shadow-2xl shadow-black/20"
    >
      <div className="mx-auto max-w-6xl px-6 py-5">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col lg:flex-row lg:items-start gap-4">
            <div className="flex-1 min-w-0">
              <p className="text-sm text-text-primary font-medium mb-1">
                We value your privacy
              </p>
              <p className="text-sm text-text-muted leading-relaxed">
                We use cookies to enhance your browsing experience and analyse site traffic.
                You can choose which categories to allow. Read our{' '}
                <a href="https://sagewai.ai/privacy" className="text-primary hover:underline">
                  Privacy Policy
                </a>{' '}
                or manage your preferences on our{' '}
                <a href="https://sagewai.ai/cookies" className="text-primary hover:underline">
                  Cookie Preferences
                </a>{' '}
                page.
              </p>
            </div>

            <div className="flex items-center gap-3 shrink-0">
              <button
                onClick={rejectAll}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-border text-text-primary hover:bg-bg-subtle transition-colors"
              >
                Reject All
              </button>
              <button
                onClick={() => setShowCustomise(!showCustomise)}
                className="px-4 py-2 text-sm font-medium rounded-lg border border-border text-text-primary hover:bg-bg-subtle transition-colors"
              >
                Customise
              </button>
              <button
                onClick={acceptAll}
                className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-bg-deep hover:bg-primary/90 transition-colors"
              >
                Accept All
              </button>
            </div>
          </div>

          {showCustomise && (
            <div className="border-t border-border-dim pt-4">
              <div className="grid gap-3 sm:grid-cols-2">
                {CATEGORIES.map((cat) => (
                  <label
                    key={cat.id}
                    className={`flex items-start gap-3 p-3 rounded-lg border border-border-dim ${
                      cat.locked ? 'opacity-70' : 'hover:border-border cursor-pointer'
                    } transition-colors`}
                  >
                    <input
                      type="checkbox"
                      checked={cat.locked || draft[cat.id as keyof typeof draft] || false}
                      disabled={cat.locked}
                      onChange={(e) => {
                        if (cat.locked) return;
                        setDraft((d) => ({ ...d, [cat.id]: e.target.checked }));
                      }}
                      className="mt-0.5 h-4 w-4 rounded border-border accent-primary"
                    />
                    <div className="min-w-0">
                      <span className="text-sm font-medium text-text-primary block">
                        {cat.label}
                      </span>
                      <span className="text-xs text-text-muted">{cat.description}</span>
                    </div>
                  </label>
                ))}
              </div>
              <div className="mt-4 flex justify-end">
                <button
                  onClick={() => {
                    updateConsent(draft);
                  }}
                  className="px-4 py-2 text-sm font-medium rounded-lg bg-primary text-bg-deep hover:bg-primary/90 transition-colors"
                >
                  Save Preferences
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
