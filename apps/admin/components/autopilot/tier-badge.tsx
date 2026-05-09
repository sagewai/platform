'use client';

// ── TierBadge ─────────────────────────────────────────────────────────────

const TIER_STYLES: Record<string, string> = {
  TRUSTED:    'bg-success/10 text-success border-success/30',
  SANDBOXED:  'bg-warning/10 text-warning border-warning/30',
  UNTRUSTED:  'bg-error/10 text-error border-error/30',
};

export function TierBadge({
  tier,
  overridden = false,
}: {
  tier: string;
  overridden?: boolean;
}) {
  const cls = TIER_STYLES[tier] ?? 'bg-bg-subtle text-text-secondary border-border';
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold font-[family-name:var(--font-mono)] ${cls}`}
      data-testid="tier-badge"
    >
      {tier}
      {overridden && (
        <span className="text-[8px] opacity-70" title="Manually overridden">
          ✎
        </span>
      )}
    </span>
  );
}
