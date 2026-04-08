'use client';

interface Column {
  key: string;
  label: string;
  className?: string;
}

interface ResponsiveTableProps {
  columns: Column[];
  rows: Record<string, React.ReactNode>[];
  emptyMessage?: string;
}

export function ResponsiveTable({ columns, rows, emptyMessage }: ResponsiveTableProps) {
  if (rows.length === 0 && emptyMessage) {
    return <p className="text-sm text-text-muted py-lg text-center">{emptyMessage}</p>;
  }

  return (
    <>
      {/* Desktop table (sm+) */}
      <div className="hidden sm:block overflow-x-auto">
        <table className="w-full text-sm border-collapse" role="table">
          <thead>
            <tr className="border-b-2 border-border">
              {columns.map((col) => (
                <th
                  key={col.key}
                  scope="col"
                  className={`text-left py-2.5 px-3 text-xs text-text-muted font-semibold uppercase tracking-wide ${col.className ?? ''}`}
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
                {columns.map((col) => (
                  <td key={col.key} className={`py-2.5 px-3 ${col.className ?? ''}`}>
                    {row[col.key]}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Mobile card layout (< sm) */}
      <div className="sm:hidden flex flex-col gap-3">
        {rows.map((row, i) => (
          <div key={i} className="bg-bg-surface border border-border rounded-lg p-3">
            {columns.map((col) => (
              <div key={col.key} className="flex justify-between items-start py-1.5">
                <span className="text-xs text-text-muted font-medium shrink-0 mr-3">{col.label}</span>
                <span className="text-sm text-right">{row[col.key]}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    </>
  );
}
