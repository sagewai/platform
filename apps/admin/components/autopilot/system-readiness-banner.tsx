// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import { useEffect, useState } from 'react';
import { AlertTriangle, Info, X, Settings } from 'lucide-react';
import { adminApi } from '@/utils/api';

type Severity = 'error' | 'warning' | 'info';
type Warning = { code: string; severity: Severity; message: string; fix: string };

const DISMISS_PREFIX = 'sagewai.autopilot.readiness.dismissed.';

const ICON_FOR_SEV: Record<Severity, typeof AlertTriangle> = {
  error: AlertTriangle,
  warning: AlertTriangle,
  info: Info,
};

const TONE_FOR_SEV: Record<Severity, string> = {
  error: 'border-error/30 bg-error/5 text-error',
  warning: 'border-warning/30 bg-warning/5 text-warning',
  info: 'border-primary/30 bg-primary/5 text-primary',
};

/**
 * Surfaces missing-config issues a first-time user is most likely to
 * miss: no LLM provider configured (autopilot won't run), no real
 * search API key (web_search degrades to scraping). Each warning is
 * dismissable per-code via localStorage so the same person doesn't
 * keep seeing it after they've seen and chosen to ignore it.
 */
export function SystemReadinessBanner() {
  const [warnings, setWarnings] = useState<Warning[] | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  useEffect(() => {
    let cancelled = false;
    adminApi
      .getAutopilotSystemReadiness()
      .then((res) => {
        if (!cancelled) setWarnings(res.warnings);
      })
      .catch(() => {
        if (!cancelled) setWarnings([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const stored = new Set<string>();
    try {
      for (let i = 0; i < window.localStorage.length; i += 1) {
        const key = window.localStorage.key(i);
        if (key && key.startsWith(DISMISS_PREFIX)) {
          stored.add(key.slice(DISMISS_PREFIX.length));
        }
      }
    } catch {
      /* private browsing in Safari etc. — silently no-op */
    }
    setDismissed(stored);
  }, []);

  function dismiss(code: string) {
    try {
      window.localStorage.setItem(`${DISMISS_PREFIX}${code}`, '1');
    } catch {
      /* ignore */
    }
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(code);
      return next;
    });
  }

  if (warnings === null || warnings.length === 0) return null;

  // Errors are never dismissable — the user truly needs to act.
  const visible = warnings.filter(
    (w) => w.severity === 'error' || !dismissed.has(w.code),
  );
  if (visible.length === 0) return null;

  return (
    <div className="space-y-2" data-testid="system-readiness">
      {visible.map((w) => {
        const Icon = ICON_FOR_SEV[w.severity];
        const tone = TONE_FOR_SEV[w.severity];
        const dismissable = w.severity !== 'error';
        return (
          <div
            key={w.code}
            role={w.severity === 'error' ? 'alert' : 'status'}
            className={`flex items-start gap-3 rounded-lg border px-4 py-3 ${tone}`}
            data-testid={`readiness-${w.code}`}
          >
            <Icon size={16} className="shrink-0 mt-0.5" aria-hidden />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium m-0 text-text-primary">{w.message}</p>
              <p className="text-xs text-text-secondary m-0 mt-1 font-[family-name:var(--font-mono)] whitespace-pre-wrap break-words">
                {w.fix}
              </p>
            </div>
            {dismissable && (
              <button
                type="button"
                onClick={() => dismiss(w.code)}
                aria-label="Dismiss"
                className="shrink-0 text-text-muted hover:text-text-secondary transition-colors"
              >
                <X size={14} aria-hidden />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}

export const SystemReadinessIcon = Settings; // re-export for sidebar use if needed
