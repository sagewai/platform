'use client';

import { useState } from 'react';
import { Button } from '@sagecurator/ui';
import { Trash2, ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react';
import { ScopeBadge } from './scope-badge';
import { SourceBadge } from './source-badge';
import type { ContextDocument } from '@/utils/types';

export type SortField = 'title' | 'status' | 'chunk_count' | 'confidence' | 'created_at' | 'source';
export type SortOrder = 'asc' | 'desc';

interface Props {
  documents: ContextDocument[];
  loading?: boolean;
  onRowClick?: (doc: ContextDocument) => void;
  onDelete?: (docId: string) => void;
  sortBy?: SortField;
  sortOrder?: SortOrder;
  onSort?: (field: SortField) => void;
  selectedIds?: Set<string>;
  onToggleSelect?: (docId: string) => void;
  onToggleSelectAll?: () => void;
  allSelected?: boolean;
}

function SortIcon({ field, sortBy, sortOrder }: { field: SortField; sortBy?: SortField; sortOrder?: SortOrder }) {
  if (sortBy !== field) return <ArrowUpDown size={12} className="text-text-muted/50" />;
  return sortOrder === 'asc'
    ? <ArrowUp size={12} className="text-primary" />
    : <ArrowDown size={12} className="text-primary" />;
}

export function DocumentTable({
  documents,
  loading,
  onRowClick,
  onDelete,
  sortBy,
  sortOrder,
  onSort,
  selectedIds,
  onToggleSelect,
  onToggleSelectAll,
  allSelected,
}: Props) {
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  if (loading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="h-14 bg-bg-subtle rounded animate-pulse" />
        ))}
      </div>
    );
  }

  if (documents.length === 0) {
    return (
      <div className="text-center py-xl text-text-muted text-sm">
        No documents found. Upload or ingest text to get started.
      </div>
    );
  }

  function headerClick(field: SortField) {
    onSort?.(field);
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-text-muted text-xs border-b border-border">
            {/* Checkbox column */}
            {onToggleSelectAll && (
              <th className="pb-2 pr-2 w-8">
                <input
                  type="checkbox"
                  checked={allSelected ?? false}
                  onChange={onToggleSelectAll}
                  className="rounded border-border"
                />
              </th>
            )}
            <th
              className="pb-2 pr-4 font-medium cursor-pointer select-none"
              onClick={() => headerClick('title')}
            >
              <span className="inline-flex items-center gap-1">
                Title <SortIcon field="title" sortBy={sortBy} sortOrder={sortOrder} />
              </span>
            </th>
            <th className="pb-2 pr-4 font-medium hidden md:table-cell">Scope / Source</th>
            <th
              className="pb-2 pr-4 font-medium cursor-pointer select-none"
              onClick={() => headerClick('status')}
            >
              <span className="inline-flex items-center gap-1">
                Status <SortIcon field="status" sortBy={sortBy} sortOrder={sortOrder} />
              </span>
            </th>
            <th
              className="pb-2 pr-4 font-medium text-right cursor-pointer select-none"
              onClick={() => headerClick('chunk_count')}
            >
              <span className="inline-flex items-center gap-1 justify-end">
                Chunks <SortIcon field="chunk_count" sortBy={sortBy} sortOrder={sortOrder} />
              </span>
            </th>
            <th
              className="pb-2 pr-4 font-medium text-right cursor-pointer select-none"
              onClick={() => headerClick('confidence')}
            >
              <span className="inline-flex items-center gap-1 justify-end">
                Confidence <SortIcon field="confidence" sortBy={sortBy} sortOrder={sortOrder} />
              </span>
            </th>
            <th
              className="pb-2 pr-4 font-medium cursor-pointer select-none"
              onClick={() => headerClick('created_at')}
            >
              <span className="inline-flex items-center gap-1">
                Created <SortIcon field="created_at" sortBy={sortBy} sortOrder={sortOrder} />
              </span>
            </th>
            <th className="pb-2 font-medium text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          {documents.map((doc) => {
            const isSelected = selectedIds?.has(doc.id) ?? false;
            return (
              <tr
                key={doc.id}
                className={`border-b border-border cursor-pointer transition-colors ${
                  isSelected ? 'bg-primary-light/40' : 'hover:bg-bg-subtle'
                }`}
                onClick={() => onRowClick?.(doc)}
              >
                {onToggleSelect && (
                  <td className="py-3 pr-2">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={(e) => {
                        e.stopPropagation();
                        onToggleSelect(doc.id);
                      }}
                      onClick={(e) => e.stopPropagation()}
                      className="rounded border-border"
                    />
                  </td>
                )}
                <td className="py-3 pr-4">
                  <div className="font-medium truncate max-w-[250px] text-text-primary">{doc.title}</div>
                  {doc.source_uri && (
                    <div className="text-[11px] text-text-muted truncate max-w-[250px]">
                      {doc.source_uri}
                    </div>
                  )}
                </td>
                <td className="py-3 pr-4 hidden md:table-cell">
                  <div className="flex items-center gap-1.5">
                    <ScopeBadge scope={doc.scope} />
                    <SourceBadge source={doc.source} />
                  </div>
                </td>
                <td className="py-3 pr-4">
                  <span className="inline-flex items-center gap-1.5">
                    {doc.status === 'processing' && (
                      <span className="relative flex h-2 w-2">
                        <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-warning opacity-75" />
                        <span className="relative inline-flex rounded-full h-2 w-2 bg-warning" />
                      </span>
                    )}
                    <span
                      className={`text-xs font-medium ${
                        doc.status === 'ready'
                          ? 'text-success'
                          : doc.status === 'processing'
                            ? 'text-warning'
                            : doc.status === 'failed'
                              ? 'text-error'
                              : 'text-text-muted'
                      }`}
                    >
                      {doc.status}
                    </span>
                  </span>
                </td>
                <td className="py-3 pr-4 text-right text-text-secondary">{doc.chunk_count}</td>
                <td className="py-3 pr-4 text-right text-text-secondary">
                  {Math.round(doc.confidence * 100)}%
                </td>
                <td className="py-3 pr-4 text-text-muted text-xs">
                  {doc.created_at
                    ? new Date(doc.created_at).toLocaleDateString()
                    : '--'}
                </td>
                <td className="py-3 text-right">
                  {confirmDelete === doc.id ? (
                    <div
                      className="flex items-center gap-1 justify-end"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(null)}>
                        Cancel
                      </Button>
                      <Button
                        size="sm"
                        variant="danger"
                        onClick={() => {
                          onDelete?.(doc.id);
                          setConfirmDelete(null);
                        }}
                      >
                        Delete
                      </Button>
                    </div>
                  ) : (
                    <button
                      className="p-1 text-text-muted hover:text-error transition-colors"
                      title="Delete document"
                      onClick={(e) => {
                        e.stopPropagation();
                        setConfirmDelete(doc.id);
                      }}
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
