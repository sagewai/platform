'use client';

interface SankeyTooltipProps {
  x: number;
  y: number;
  label: string;
  tokens: number;
  cost: number;
  visible: boolean;
}

export function SankeyTooltip({ x, y, label, tokens, cost, visible }: SankeyTooltipProps) {
  if (!visible) return null;

  return (
    <div
      className="fixed z-50 pointer-events-none bg-bg-elevated border border-border rounded-lg shadow-xl px-3 py-2 text-xs"
      style={{ left: x + 12, top: y - 20 }}
    >
      <div className="font-semibold text-text-primary mb-1">{label}</div>
      <div className="text-text-muted">
        {tokens.toLocaleString()} tokens &middot; ${cost.toFixed(4)}
      </div>
    </div>
  );
}
