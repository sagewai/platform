// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
import { AlertCircle } from 'lucide-react';

interface AutopilotQuotaExceededProps {
  /** ISO-8601 timestamp when the monthly window resets, if known. */
  resetAt?: string | null;
  used?: number;
  limit?: number;
}

function formatReset(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' });
  } catch {
    return iso;
  }
}

export function AutopilotQuotaExceeded({ resetAt, used, limit }: AutopilotQuotaExceededProps) {
  const body = [
    'Hi,',
    '',
    "I've reached my Autopilot monthly limit and would like to raise it.",
    used != null && limit != null ? `Current usage: ${used} / ${limit} missions.` : '',
    resetAt ? `Current window resets: ${resetAt}.` : '',
    '',
    'Please let me know what options are available.',
  ]
    .filter((l) => l !== undefined)
    .join('\n');

  const mailto = `mailto:licensing@sagewai.ai?subject=${encodeURIComponent('Autopilot limit increase request')}&body=${encodeURIComponent(body)}`;

  return (
    <div
      data-testid="autopilot-quota-exceeded"
      className="flex items-start gap-3 bg-warning/5 border border-warning/30 rounded-xl px-5 py-4"
    >
      <AlertCircle className="w-5 h-5 text-warning shrink-0 mt-0.5" />
      <div className="flex-1 space-y-1.5">
        <p className="text-sm font-semibold text-text-primary m-0">
          Monthly mission limit reached
        </p>
        <p className="text-xs text-text-secondary m-0">
          {resetAt
            ? `Your free limit resets on ${formatReset(resetAt)}.`
            : 'Your free limit has been reached for this month.'}
          {' '}To run more missions now, contact us to raise your limit.
        </p>
        <a
          href={mailto}
          className="inline-block text-xs font-medium text-primary hover:underline underline-offset-2"
        >
          Contact licensing@sagewai.ai →
        </a>
      </div>
    </div>
  );
}
