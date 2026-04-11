'use client';

interface HeatmapControlsProps {
  days: number;
  onDaysChange: (d: number) => void;
  filter: string;
  onFilterChange: (v: string) => void;
}

export function HeatmapControls({ days, onDaysChange, filter, onFilterChange }: HeatmapControlsProps) {
  return (
    <div className="flex flex-wrap items-end gap-3 mb-4">
      <div>
        <label className="block text-[10px] uppercase text-text-muted font-semibold mb-1">
          Time Range
        </label>
        <div className="flex gap-1">
          {[30, 60, 90].map((d) => (
            <button
              key={d}
              type="button"
              onClick={() => onDaysChange(d)}
              className={`px-3 py-1.5 text-xs border rounded cursor-pointer transition-colors ${
                days === d
                  ? 'bg-primary text-white border-primary'
                  : 'bg-bg-surface text-text-muted border-border hover:border-primary'
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>
      <div>
        <label className="block text-[10px] uppercase text-text-muted font-semibold mb-1">
          Filter Workflow
        </label>
        <input
          type="text"
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
          placeholder="Workflow name..."
          className="px-2 py-1.5 text-xs border border-border rounded bg-bg-surface text-text-primary w-48"
        />
      </div>
    </div>
  );
}
