'use client';

import { useEffect, useState, use } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Card, Button, Skeleton, useToast } from '@sagecurator/ui';
import { RefreshCw, Trash2, ArrowLeft } from 'lucide-react';
import { adminApi } from '@/utils/api';
import type { ContextDocument, ContextChunk } from '@/utils/types';
import { ScopeBadge } from '@/components/scope-badge';
import { SourceBadge } from '@/components/source-badge';
import { ChunkList } from '@/components/chunk-list';

interface Props {
  params: Promise<{ id: string }>;
}

export default function DocumentDetailPage({ params }: Props) {
  const { id } = use(params);
  const router = useRouter();
  const { toast } = useToast();
  const [doc, setDoc] = useState<ContextDocument | null>(null);
  const [chunks, setChunks] = useState<ContextChunk[]>([]);
  const [loading, setLoading] = useState(true);
  const [reprocessing, setReprocessing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  async function loadDocument() {
    try {
      const data = await adminApi.getContextDocument(id);
      setDoc(data.document);
      setChunks(data.chunks);
    } catch {
      setDoc(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { loadDocument(); }, [id]);

  async function handleReprocess() {
    setReprocessing(true);
    try {
      await adminApi.reprocessContextDocument(id);
      toast('success', 'Document reprocessing started');
      await loadDocument();
    } catch (e) {
      toast('error', `Reprocess failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setReprocessing(false);
    }
  }

  async function handleDelete() {
    try {
      await adminApi.deleteContextDocument(id);
      toast('success', 'Document deleted');
      router.push('/context/documents');
    } catch (e) {
      toast('error', `Delete failed: ${e instanceof Error ? e.message : 'unknown'}`);
    }
  }

  async function handleChunkUpdate(chunkId: string, body: { content?: string; importance?: number }) {
    await adminApi.updateContextChunk(chunkId, body);
    await loadDocument();
  }

  async function handleChunkDelete(chunkId: string) {
    await adminApi.deleteContextChunk(chunkId);
    await loadDocument();
  }

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto">
        <Skeleton lines={1} className="w-32 mb-md" />
        <Skeleton lines={3} className="mb-lg" />
        <Skeleton lines={5} />
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="max-w-4xl mx-auto">
        <Link href="/context/documents" className="text-primary no-underline text-sm flex items-center gap-1">
          <ArrowLeft size={14} /> Back to documents
        </Link>
        <h1 className="mt-md text-xl font-bold font-[family-name:var(--font-heading)]">Document not found</h1>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto">
      <Link href="/context/documents" className="text-primary no-underline text-sm flex items-center gap-1 mb-md">
        <ArrowLeft size={14} /> Back to documents
      </Link>

      <div className="flex items-start justify-between mb-lg">
        <div>
          <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-2">{doc.title}</h1>
          <div className="flex items-center gap-2">
            <ScopeBadge scope={doc.scope} />
            <SourceBadge source={doc.source} />
            <span className={`text-xs font-medium ${
              doc.status === 'ready' ? 'text-green-400' :
              doc.status === 'processing' ? 'text-yellow-400' :
              doc.status === 'failed' ? 'text-red-400' : 'text-text-muted'
            }`}>
              {doc.status}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={handleReprocess} disabled={reprocessing}>
            <RefreshCw size={14} className={`mr-1 ${reprocessing ? 'animate-spin' : ''}`} />
            {reprocessing ? 'Reprocessing...' : 'Reprocess'}
          </Button>
          {confirmDelete ? (
            <div className="flex items-center gap-1">
              <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(false)}>Cancel</Button>
              <Button size="sm" variant="danger" onClick={handleDelete}>Confirm Delete</Button>
            </div>
          ) : (
            <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(true)}>
              <Trash2 size={14} className="mr-1" /> Delete
            </Button>
          )}
        </div>
      </div>

      <Card className="p-md mb-lg">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-md text-sm">
          <div>
            <div className="text-text-muted text-xs mb-0.5">Confidence</div>
            <div className="font-medium">{Math.round(doc.confidence * 100)}%</div>
          </div>
          <div>
            <div className="text-text-muted text-xs mb-0.5">Chunks</div>
            <div className="font-medium">{doc.chunk_count}</div>
          </div>
          <div>
            <div className="text-text-muted text-xs mb-0.5">File Size</div>
            <div className="font-medium">
              {doc.file_size_bytes ? `${(doc.file_size_bytes / 1024).toFixed(1)} KB` : '—'}
            </div>
          </div>
          <div>
            <div className="text-text-muted text-xs mb-0.5">MIME Type</div>
            <div className="font-medium font-mono text-xs">{doc.mime_type ?? '—'}</div>
          </div>
          {doc.source_uri && (
            <div className="col-span-2">
              <div className="text-text-muted text-xs mb-0.5">Source URI</div>
              <div className="font-mono text-xs truncate">{doc.source_uri}</div>
            </div>
          )}
          <div>
            <div className="text-text-muted text-xs mb-0.5">Created</div>
            <div className="font-medium text-xs">{doc.created_at ? new Date(doc.created_at).toLocaleDateString() : '—'}</div>
          </div>
          <div>
            <div className="text-text-muted text-xs mb-0.5">Updated</div>
            <div className="font-medium text-xs">{doc.updated_at ? new Date(doc.updated_at).toLocaleDateString() : '—'}</div>
          </div>
        </div>
      </Card>

      <div className="mb-md">
        <h2 className="text-lg font-bold font-[family-name:var(--font-heading)] mb-md">
          Chunks ({chunks.length})
        </h2>
        <ChunkList
          chunks={chunks}
          onUpdate={handleChunkUpdate}
          onDelete={handleChunkDelete}
        />
      </div>
    </div>
  );
}
