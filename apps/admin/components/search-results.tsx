'use client';

import Link from 'next/link';
import { Card } from '@sagecurator/ui';
import { ScopeBadge } from './scope-badge';
import { SourceBadge } from './source-badge';
import type { ContextSearchResult } from '@/utils/types';

interface Props {
  results: ContextSearchResult[];
  query: string;
}

export function SearchResults({ results, query }: Props) {
  if (results.length === 0) {
    return (
      <div className="text-center py-xl text-text-muted text-sm">
        No results found for &quot;{query}&quot;
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="text-xs text-text-muted mb-2">{results.length} results</div>
      {results.map((r, idx) => (
        <Card key={`${r.chunk_id}-${idx}`} className="p-md hover:bg-white/[0.03] transition-colors">
          <div className="flex items-start justify-between gap-3 mb-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono text-text-muted">#{idx + 1}</span>
              <ScopeBadge scope={r.scope} />
              <SourceBadge source={r.source} />
            </div>
            <div className="flex items-center gap-2">
              <div className="w-20 h-1.5 bg-white/10 rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full"
                  style={{ width: `${Math.round(r.score * 100)}%` }}
                />
              </div>
              <span className="text-xs font-mono text-text-muted">{(r.score * 100).toFixed(1)}%</span>
            </div>
          </div>
          <div className="text-sm whitespace-pre-wrap break-words leading-relaxed mb-2">
            {r.content.length > 400 ? r.content.slice(0, 400) + '...' : r.content}
          </div>
          <div className="flex items-center gap-2 text-[11px] text-text-muted">
            <Link
              href={`/context/documents/${r.document_id}`}
              className="text-primary no-underline hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {r.document_title}
            </Link>
          </div>
        </Card>
      ))}
    </div>
  );
}
