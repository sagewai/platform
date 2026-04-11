'use client';

const SOURCE_COLORS: Record<string, string> = {
  upload: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
  manual: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
  directory: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  url: 'bg-indigo-500/15 text-indigo-400 border-indigo-500/30',
  conversation: 'bg-rose-500/15 text-rose-400 border-rose-500/30',
  api: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
};

export function SourceBadge({ source }: { source: string }) {
  const colorClass = SOURCE_COLORS[source] ?? 'bg-gray-500/15 text-gray-400 border-gray-500/30';
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium border ${colorClass}`}
    >
      {source}
    </span>
  );
}
