interface ModelBadgeProps {
  name: string;
  provider: string;
}

export function ModelBadge({ name, provider }: ModelBadgeProps) {
  return (
    <div className="flex items-center gap-2 px-4 py-2.5 bg-bg-elevated rounded-lg border border-border hover:border-primary/40 transition-colors">
      <span className="text-sm font-medium text-text-primary">{name}</span>
      <span className="text-xs text-text-muted">{provider}</span>
    </div>
  );
}
