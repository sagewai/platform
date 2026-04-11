'use client';

interface NetworkControlsProps {
  fromDate: string;
  toDate: string;
  onFromChange: (v: string) => void;
  onToChange: (v: string) => void;
  layoutMode: 'force' | 'hierarchical' | 'circular';
  onLayoutChange: (v: 'force' | 'hierarchical' | 'circular') => void;
  filter: string;
  onFilterChange: (v: string) => void;
}

export function NetworkControls({
  fromDate,
  toDate,
  onFromChange,
  onToChange,
  layoutMode,
  onLayoutChange,
  filter,
  onFilterChange,
}: NetworkControlsProps) {
  return (
    <div className="flex flex-wrap items-end gap-3 mb-4">
      <div>
        <label className="block text-[10px] uppercase text-text-muted font-semibold mb-1">
          From
        </label>
        <input
          type="date"
          value={fromDate}
          onChange={(e) => onFromChange(e.target.value)}
          className="px-2 py-1.5 text-xs border border-border rounded bg-bg-surface text-text-primary"
        />
      </div>
      <div>
        <label className="block text-[10px] uppercase text-text-muted font-semibold mb-1">
          To
        </label>
        <input
          type="date"
          value={toDate}
          onChange={(e) => onToChange(e.target.value)}
          className="px-2 py-1.5 text-xs border border-border rounded bg-bg-surface text-text-primary"
        />
      </div>
      <div>
        <label className="block text-[10px] uppercase text-text-muted font-semibold mb-1">
          Layout
        </label>
        <select
          value={layoutMode}
          onChange={(e) => onLayoutChange(e.target.value as 'force' | 'hierarchical' | 'circular')}
          className="px-2 py-1.5 text-xs border border-border rounded bg-bg-surface text-text-primary"
        >
          <option value="force">Force-Directed</option>
          <option value="hierarchical" disabled>
            Hierarchical (coming soon)
          </option>
          <option value="circular" disabled>
            Circular (coming soon)
          </option>
        </select>
      </div>
      <div>
        <label className="block text-[10px] uppercase text-text-muted font-semibold mb-1">
          Filter
        </label>
        <input
          type="text"
          value={filter}
          onChange={(e) => onFilterChange(e.target.value)}
          placeholder="Workflow name..."
          className="px-2 py-1.5 text-xs border border-border rounded bg-bg-surface text-text-primary w-40"
        />
      </div>
    </div>
  );
}
