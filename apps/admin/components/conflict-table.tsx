'use client';

import { useState } from 'react';
import { Card, Button, useToast } from '@/components/ui/legacy';
import { AlertTriangle, Check } from 'lucide-react';
import { ScopeBadge } from './scope-badge';
import { adminApi } from '@/utils/api';
import type { ContextConflict } from '@/utils/types';

interface Props {
  conflicts: ContextConflict[];
  loading?: boolean;
  onResolved: () => void;
}

export function ConflictTable({ conflicts, loading, onResolved }: Props) {
  const [resolving, setResolving] = useState<string | null>(null);
  const { toast } = useToast();

  if (loading) {
    return (
      <div className="space-y-2">
        {[1, 2].map((i) => (
          <div key={i} className="h-24 bg-bg-subtle rounded animate-pulse" />
        ))}
      </div>
    );
  }

  if (conflicts.length === 0) {
    return (
      <div className="text-center py-lg text-text-muted text-sm">
        <Check size={20} className="mx-auto mb-2 text-green-400" />
        No conflicts detected. Knowledge base is consistent.
      </div>
    );
  }

  async function resolve(keepId: string, discardId: string) {
    setResolving(keepId);
    try {
      await adminApi.resolveContextConflict(keepId, discardId);
      toast('success', 'Conflict resolved');
      onResolved();
    } catch (e) {
      toast('error', `Failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setResolving(null);
    }
  }

  return (
    <div className="space-y-3">
      {conflicts.map((c, idx) => (
        <Card key={`${c.chunk_a_id}-${c.chunk_b_id}`} className="p-md">
          <div className="flex items-center gap-2 mb-3">
            <AlertTriangle size={14} className="text-amber-400" />
            <span className="text-xs font-medium text-amber-400">
              {Math.round(c.similarity * 100)}% similar
            </span>
            <ScopeBadge scope={c.scope} />
          </div>

          <div className="grid grid-cols-2 gap-3 mb-3">
            <div className="border border-border rounded p-2">
              <div className="text-[10px] text-text-muted mb-1 font-mono">Chunk A</div>
              <div className="text-xs leading-relaxed">
                {c.chunk_a_content.length > 200 ? c.chunk_a_content.slice(0, 200) + '...' : c.chunk_a_content}
              </div>
            </div>
            <div className="border border-border rounded p-2">
              <div className="text-[10px] text-text-muted mb-1 font-mono">Chunk B</div>
              <div className="text-xs leading-relaxed">
                {c.chunk_b_content.length > 200 ? c.chunk_b_content.slice(0, 200) + '...' : c.chunk_b_content}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 justify-end">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => resolve(c.chunk_a_id, c.chunk_b_id)}
              disabled={resolving === c.chunk_a_id}
            >
              Keep A
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => resolve(c.chunk_b_id, c.chunk_a_id)}
              disabled={resolving === c.chunk_b_id}
            >
              Keep B
            </Button>
          </div>
        </Card>
      ))}
    </div>
  );
}
