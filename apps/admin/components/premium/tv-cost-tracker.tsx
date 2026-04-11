'use client';

interface CostData {
  totalCost: number;
  costByModel: Record<string, number>;
}

export function TVCostTracker({ data }: { data: CostData }) {
  const { totalCost, costByModel } = data;
  const entries = Object.entries(costByModel).sort(([, a], [, b]) => b - a);
  const maxCost = entries.length > 0 ? entries[0][1] : 1;

  return (
    <div className="flex flex-col h-full p-12">
      {/* Today's total cost */}
      <div className="text-center mb-12">
        <div className="text-white/40 text-sm uppercase tracking-widest mb-2">
          Total Cost (Today)
        </div>
        <div className="text-7xl font-bold text-[#26C6DA] tabular-nums">
          ${totalCost.toFixed(4)}
        </div>
      </div>

      {/* Cost by model */}
      <div className="flex-1">
        <div className="text-white/40 text-xs uppercase tracking-widest mb-4">Cost by Model</div>
        <div className="space-y-3">
          {entries.length === 0 && (
            <div className="text-white/30 text-center py-8">No cost data available</div>
          )}
          {entries.slice(0, 8).map(([model, cost]) => {
            const pct = maxCost > 0 ? (cost / maxCost) * 100 : 0;
            return (
              <div key={model}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-white/70 truncate mr-4">{model}</span>
                  <span className="text-white/50 tabular-nums shrink-0">${cost.toFixed(4)}</span>
                </div>
                <div className="h-2 bg-white/10 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-[#9C27B0] via-[#FF7043] to-[#26C6DA] rounded-full transition-all duration-700"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
