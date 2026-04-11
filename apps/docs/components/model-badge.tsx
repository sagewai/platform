interface ModelBadgeProps {
  name: string;
  provider: string;
}

export function ModelBadge({ name, provider }: ModelBadgeProps) {
  return (
    <div className="flex items-center gap-2 px-4 py-2.5 bg-white rounded-lg border border-gray-200 hover:border-emerald-300 transition-colors">
      <span className="text-sm font-medium text-gray-900">{name}</span>
      <span className="text-xs text-gray-500">{provider}</span>
    </div>
  );
}
