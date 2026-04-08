interface StatCardProps {
  label: string;
  value: string | number;
}

export function StatCard({ label, value }: StatCardProps) {
  return (
    <div className="bg-surface-dark rounded-lg border border-border-dark p-md hover:border-primary/30 transition-colors">
      <div className="text-xs text-text-muted uppercase tracking-wide">
        {label}
      </div>
      <div className="text-2xl font-bold mt-1 font-[family-name:var(--font-heading)] text-text-on-dark">{value}</div>
    </div>
  );
}
