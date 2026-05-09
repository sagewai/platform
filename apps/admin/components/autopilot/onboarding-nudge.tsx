// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import { useEffect, useState } from 'react';
import { X, Zap } from 'lucide-react';

const DISMISSED_KEY = 'sagewai.autopilot.onboarding.dismissed';

export function OnboardingNudge() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    try {
      setShow(localStorage.getItem(DISMISSED_KEY) !== '1');
    } catch {
      // localStorage unavailable (e.g., private browsing in Safari) — silently skip.
    }
  }, []);

  if (!show) return null;

  function dismiss() {
    try {
      localStorage.setItem(DISMISSED_KEY, '1');
    } catch {
      // ignore
    }
    setShow(false);
  }

  return (
    <div
      role="region"
      aria-label="Onboarding tip"
      data-testid="onboarding-nudge"
      className="rounded-lg border border-primary/30 bg-primary/5 px-4 py-3 flex items-start gap-3"
    >
      <Zap className="size-4 text-primary shrink-0 mt-0.5" aria-hidden />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-text-primary m-0">New here?</p>
        <p className="text-xs text-text-secondary m-0 mt-0.5">
          Start with a goal in plain English — Sagewai will find a blueprint and run it for you.
        </p>
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss onboarding tip"
        data-testid="dismiss-nudge"
        className="shrink-0 text-text-muted hover:text-text-secondary transition-colors"
      >
        <X className="size-4" aria-hidden />
      </button>
    </div>
  );
}
