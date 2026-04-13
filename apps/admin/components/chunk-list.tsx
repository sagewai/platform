'use client';

import { useState } from 'react';
import { Button, TextArea, useToast } from '@/components/ui/legacy';
import { Pencil, Trash2, X, Check } from 'lucide-react';
import type { ContextChunk } from '@/utils/types';

interface Props {
  chunks: ContextChunk[];
  loading?: boolean;
  onUpdate?: (chunkId: string, body: { content?: string; importance?: number }) => Promise<void>;
  onDelete?: (chunkId: string) => Promise<void>;
}

export function ChunkList({ chunks, loading, onUpdate, onDelete }: Props) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [editImportance, setEditImportance] = useState(0.5);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const { toast } = useToast();

  if (loading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-20 bg-bg-subtle rounded animate-pulse" />
        ))}
      </div>
    );
  }

  if (chunks.length === 0) {
    return <div className="text-center py-lg text-text-muted text-sm">No chunks found.</div>;
  }

  function startEdit(chunk: ContextChunk) {
    setEditingId(chunk.id);
    setEditContent(chunk.content);
    setEditImportance(chunk.importance);
  }

  async function saveEdit(chunkId: string) {
    setSaving(true);
    try {
      await onUpdate?.(chunkId, { content: editContent, importance: editImportance });
      setEditingId(null);
      toast('success', 'Chunk updated');
    } catch (e) {
      toast('error', `Failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(chunkId: string) {
    try {
      await onDelete?.(chunkId);
      setConfirmDeleteId(null);
      toast('success', 'Chunk deleted');
    } catch (e) {
      toast('error', `Failed: ${e instanceof Error ? e.message : 'unknown'}`);
    }
  }

  return (
    <div className="space-y-3">
      {chunks.map((chunk) => (
        <div key={chunk.id} className="border border-border rounded-lg p-md bg-bg-surface">
          <div className="flex items-start justify-between gap-3 mb-2">
            <div className="flex items-center gap-2 text-xs text-text-muted">
              <span className="font-mono">#{chunk.chunk_index}</span>
              <span>{chunk.token_count} tokens</span>
              <span>importance: {(chunk.importance * 100).toFixed(0)}%</span>
              <span>accessed: {chunk.access_count}x</span>
            </div>
            <div className="flex items-center gap-1">
              {editingId !== chunk.id && (
                <>
                  <button
                    onClick={() => startEdit(chunk)}
                    className="p-1 text-text-muted hover:text-text-primary transition-colors"
                    title="Edit chunk"
                  >
                    <Pencil size={13} />
                  </button>
                  {confirmDeleteId === chunk.id ? (
                    <div className="flex items-center gap-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => setConfirmDeleteId(null)}
                      >
                        Cancel
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => handleDelete(chunk.id)}
                      >
                        Delete
                      </Button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setConfirmDeleteId(chunk.id)}
                      className="p-1 text-text-muted hover:text-red-400 transition-colors"
                      title="Delete chunk"
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </>
              )}
            </div>
          </div>

          {editingId === chunk.id ? (
            <div className="space-y-2">
              <TextArea value={editContent} onChange={(e) => setEditContent(e.target.value)} rows={4} />
              <div className="flex items-center gap-3">
                <label className="text-xs text-text-muted">Importance</label>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.01}
                  value={editImportance}
                  onChange={(e) => setEditImportance(parseFloat(e.target.value))}
                  className="flex-1"
                />
                <span className="text-xs w-10 text-right">
                  {(editImportance * 100).toFixed(0)}%
                </span>
              </div>
              <div className="flex items-center gap-2 justify-end">
                <Button size="sm" variant="ghost" onClick={() => setEditingId(null)}>
                  <X size={13} className="mr-1" /> Cancel
                </Button>
                <Button size="sm" onClick={() => saveEdit(chunk.id)} disabled={saving}>
                  <Check size={13} className="mr-1" /> {saving ? 'Saving...' : 'Save'}
                </Button>
              </div>
            </div>
          ) : (
            <div className="text-sm whitespace-pre-wrap break-words text-text-muted leading-relaxed">
              {chunk.content.length > 500 ? chunk.content.slice(0, 500) + '...' : chunk.content}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
