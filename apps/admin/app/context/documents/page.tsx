'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Button, useToast } from '@/components/ui/legacy';
import { Plus, Search, ChevronLeft, ChevronRight } from 'lucide-react';
import { adminApi } from '@/utils/api';
import type { ContextDocument } from '@/utils/types';
import { DocumentTable, type SortField, type SortOrder } from '@/components/document-table';
import { DocumentUploadDialog } from '@/components/document-upload-dialog';

const SCOPES = ['', 'org', 'project'];
const SOURCES = ['', 'upload', 'manual', 'directory', 'url', 'conversation', 'api'];
const STATUSES = ['', 'ready', 'processing', 'failed', 'archived'];
const PAGE_SIZE = 20;

export default function DocumentsPage() {
  const router = useRouter();
  const { toast } = useToast();
  const [documents, setDocuments] = useState<ContextDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [uploadOpen, setUploadOpen] = useState(false);

  // Search
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const searchTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Filters
  const [filterScope, setFilterScope] = useState('');
  const [filterSource, setFilterSource] = useState('');
  const [filterStatus, setFilterStatus] = useState('');

  // Sort
  const [sortBy, setSortBy] = useState<SortField>('created_at');
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc');

  // Pagination
  const [page, setPage] = useState(0);

  // Multi-select
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [confirmBatch, setConfirmBatch] = useState(false);

  // Processing poll
  const [hasProcessing, setHasProcessing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval>>(undefined);
  const prevProcessingRef = useRef<Set<string>>(new Set());

  // Debounce search
  useEffect(() => {
    if (searchTimer.current) clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => {
      setDebouncedSearch(searchQuery);
      setPage(0);
    }, 300);
    return () => { if (searchTimer.current) clearTimeout(searchTimer.current); };
  }, [searchQuery]);

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = {
        sort_by: sortBy,
        sort_order: sortOrder,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      };
      if (filterScope) params.scope = filterScope;
      if (filterSource) params.source = filterSource;
      if (filterStatus) params.status = filterStatus;
      if (debouncedSearch) params.search = debouncedSearch;
      const data = await adminApi.listContextDocuments(params as Record<string, string>);
      setDocuments(data.documents);
      setTotal(data.total);

      // Track processing docs
      const processingDocs = data.documents.filter((d) => d.status === 'processing');
      const processingIds = new Set(processingDocs.map((d) => d.id));
      setHasProcessing(processingDocs.length > 0);

      // Toast when processing completes
      for (const id of prevProcessingRef.current) {
        if (!processingIds.has(id)) {
          const doc = data.documents.find((d) => d.id === id);
          if (doc && doc.status === 'ready') {
            toast('success', `"${doc.title}" finished processing`);
          }
        }
      }
      prevProcessingRef.current = processingIds;
    } catch {
      setDocuments([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [filterScope, filterSource, filterStatus, debouncedSearch, sortBy, sortOrder, page, toast]);

  useEffect(() => { loadDocuments(); }, [loadDocuments]);

  // Poll for processing documents
  useEffect(() => {
    if (hasProcessing) {
      pollRef.current = setInterval(loadDocuments, 3000);
    } else if (pollRef.current) {
      clearInterval(pollRef.current);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [hasProcessing, loadDocuments]);

  function handleSort(field: SortField) {
    if (sortBy === field) {
      setSortOrder((prev) => (prev === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(field);
      setSortOrder('asc');
    }
    setPage(0);
  }

  function toggleSelect(docId: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(docId)) next.delete(docId);
      else next.add(docId);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selectedIds.size === documents.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(documents.map((d) => d.id)));
    }
  }

  async function handleDelete(docId: string) {
    try {
      await adminApi.deleteContextDocument(docId);
      toast('success', 'Document deleted');
      setSelectedIds((prev) => { const next = new Set(prev); next.delete(docId); return next; });
      loadDocuments();
    } catch (e) {
      toast('error', `Delete failed: ${e instanceof Error ? e.message : 'unknown'}`);
    }
  }

  async function handleBatchDelete() {
    if (selectedIds.size === 0) return;
    setBatchDeleting(true);
    try {
      await adminApi.batchDeleteContextDocuments(Array.from(selectedIds));
      toast('success', `${selectedIds.size} document(s) deleted`);
      setSelectedIds(new Set());
      setConfirmBatch(false);
      loadDocuments();
    } catch (e) {
      toast('error', `Batch delete failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setBatchDeleting(false);
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const showFrom = total > 0 ? page * PAGE_SIZE + 1 : 0;
  const showTo = Math.min((page + 1) * PAGE_SIZE, total);

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-lg">
        <div>
          <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-1 text-text-primary">
            Documents
          </h1>
          <p className="text-text-muted text-sm">
            Manage knowledge documents across all context scopes
          </p>
        </div>
        <Button onClick={() => setUploadOpen(true)}>
          <Plus size={14} className="mr-1" /> Add Knowledge
        </Button>
      </div>

      {/* Search bar */}
      <div className="relative mb-md">
        <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search by title, tags, or content..."
          className="w-full pl-9 pr-3 py-2 bg-bg-surface border border-border rounded-lg text-sm text-text-primary focus:outline-none focus:border-primary"
        />
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-3 mb-md">
        <select
          value={filterScope}
          onChange={(e) => { setFilterScope(e.target.value); setPage(0); }}
          className="bg-bg-surface border border-border rounded px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:border-primary"
        >
          <option value="">All Scopes</option>
          {SCOPES.filter(Boolean).map((s) => (
            <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
          ))}
        </select>
        <select
          value={filterSource}
          onChange={(e) => { setFilterSource(e.target.value); setPage(0); }}
          className="bg-bg-surface border border-border rounded px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:border-primary"
        >
          <option value="">All Sources</option>
          {SOURCES.filter(Boolean).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <select
          value={filterStatus}
          onChange={(e) => { setFilterStatus(e.target.value); setPage(0); }}
          className="bg-bg-surface border border-border rounded px-2.5 py-1.5 text-xs text-text-primary focus:outline-none focus:border-primary"
        >
          <option value="">All Statuses</option>
          {STATUSES.filter(Boolean).map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        {(filterScope || filterSource || filterStatus) && (
          <button
            onClick={() => { setFilterScope(''); setFilterSource(''); setFilterStatus(''); setPage(0); }}
            className="text-xs text-primary hover:underline"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Processing banner */}
      {hasProcessing && (
        <div className="flex items-center gap-2 mb-md px-4 py-2.5 bg-warning/10 border border-warning/20 rounded-lg text-sm text-warning">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-warning opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-warning" />
          </span>
          Documents are being processed. This page will auto-refresh.
        </div>
      )}

      {/* Batch action bar */}
      {selectedIds.size > 0 && (
        <div className="flex items-center gap-3 mb-md px-4 py-2.5 bg-bg-subtle border border-border rounded-lg text-sm">
          <span className="text-text-secondary font-medium">
            {selectedIds.size} selected
          </span>
          <span className="text-border">|</span>
          {confirmBatch ? (
            <div className="flex items-center gap-2">
              <span className="text-error text-xs">Are you sure?</span>
              <Button
                size="sm"
                variant="danger"
                onClick={handleBatchDelete}
                disabled={batchDeleting}
              >
                {batchDeleting ? 'Deleting...' : 'Confirm Delete'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmBatch(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmBatch(true)}
              className="text-xs text-error hover:underline"
            >
              Delete Selected
            </button>
          )}
        </div>
      )}

      {/* Table */}
      <DocumentTable
        documents={documents}
        loading={loading}
        onRowClick={(doc) => router.push(`/context/documents/${doc.id}`)}
        onDelete={handleDelete}
        sortBy={sortBy}
        sortOrder={sortOrder}
        onSort={handleSort}
        selectedIds={selectedIds}
        onToggleSelect={toggleSelect}
        onToggleSelectAll={toggleSelectAll}
        allSelected={documents.length > 0 && selectedIds.size === documents.length}
      />

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-md text-sm text-text-secondary">
          <span>
            Showing {showFrom}--{showTo} of {total}
          </span>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="p-1.5 rounded hover:bg-bg-subtle disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft size={16} />
            </button>
            {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
              let pageNum: number;
              if (totalPages <= 7) {
                pageNum = i;
              } else if (page < 3) {
                pageNum = i;
              } else if (page > totalPages - 4) {
                pageNum = totalPages - 7 + i;
              } else {
                pageNum = page - 3 + i;
              }
              return (
                <button
                  key={pageNum}
                  onClick={() => setPage(pageNum)}
                  className={`min-w-[28px] h-7 rounded text-xs transition-colors ${
                    page === pageNum
                      ? 'bg-primary text-white font-medium'
                      : 'hover:bg-bg-subtle text-text-secondary'
                  }`}
                >
                  {pageNum + 1}
                </button>
              );
            })}
            <button
              onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              disabled={page >= totalPages - 1}
              className="p-1.5 rounded hover:bg-bg-subtle disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
      )}

      <DocumentUploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onSuccess={loadDocuments}
      />
    </div>
  );
}
