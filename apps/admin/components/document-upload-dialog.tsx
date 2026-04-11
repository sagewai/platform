'use client';

import { useState, useRef } from 'react';
import { createPortal } from 'react-dom';
import { Button, FormField, TextInput, TextArea, Tabs, useToast } from '@/components/ui/legacy';
import {
  Upload, X, FileText, CheckCircle2, AlertCircle, Loader2, FolderOpen,
} from 'lucide-react';
import { adminApi } from '@/utils/api';
import { ScopeSelector } from './scope-selector';

type FileStatus = 'queued' | 'uploading' | 'done' | 'error';

interface QueuedFile {
  file: File;
  status: FileStatus;
  error?: string;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

export function DocumentUploadDialog({ open, onClose, onSuccess }: Props) {
  const [tab, setTab] = useState('file');
  const [scope, setScope] = useState('org');
  const [scopeId, setScopeId] = useState('');
  const [tags, setTags] = useState('');
  const [uploading, setUploading] = useState(false);
  const { toast } = useToast();

  const [fileQueue, setFileQueue] = useState<QueuedFile[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const dirRef = useRef<HTMLInputElement>(null);

  const [title, setTitle] = useState('');
  const [text, setText] = useState('');
  const [dragOver, setDragOver] = useState(false);

  if (!open) return null;

  const parsedTags = tags.split(',').map((t) => t.trim()).filter(Boolean);

  function addFiles(files: FileList | File[]) {
    const arr = Array.from(files);
    setFileQueue((prev) => {
      const existing = new Set(prev.map((q) => `${q.file.name}:${q.file.size}`));
      const newFiles = arr
        .filter((f) => !existing.has(`${f.name}:${f.size}`))
        .map((file) => ({ file, status: 'queued' as FileStatus }));
      return [...prev, ...newFiles];
    });
  }

  function removeFile(idx: number) {
    setFileQueue((prev) => prev.filter((_, i) => i !== idx));
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function handleFileUpload() {
    if (fileQueue.length === 0) return;
    setUploading(true);

    const queue = [...fileQueue];
    const concurrency = 3;
    let idx = 0;
    let errorCount = 0;

    async function uploadNext() {
      while (idx < queue.length) {
        const i = idx++;
        setFileQueue((prev) =>
          prev.map((q, j) => (j === i ? { ...q, status: 'uploading' } : q)),
        );
        try {
          await adminApi.uploadContextDocument(
            queue[i].file, scope, scopeId, false, parsedTags,
          );
          setFileQueue((prev) =>
            prev.map((q, j) => (j === i ? { ...q, status: 'done' } : q)),
          );
        } catch (e) {
          errorCount++;
          setFileQueue((prev) =>
            prev.map((q, j) =>
              j === i
                ? { ...q, status: 'error', error: e instanceof Error ? e.message : 'Upload failed' }
                : q,
            ),
          );
        }
      }
    }

    const workers = Array.from({ length: Math.min(concurrency, queue.length) }, () => uploadNext());
    await Promise.all(workers);

    setUploading(false);
    const doneCount = queue.length - errorCount;
    if (doneCount > 0) {
      toast('success', `${doneCount} file(s) queued for processing`);
      onSuccess();
    }
    if (errorCount > 0) {
      toast('error', `${errorCount} file(s) failed to upload`);
    }
  }

  async function handleTextIngest() {
    if (!title.trim() || !text.trim()) return;
    setUploading(true);
    try {
      await adminApi.ingestContextText({
        text, title, scope, scope_id: scopeId, tags: parsedTags,
      });
      toast('success', `'${title}' queued for processing`);
      onSuccess();
      resetAndClose();
    } catch (e) {
      toast('error', `Ingestion failed: ${e instanceof Error ? e.message : 'unknown'}`);
    } finally {
      setUploading(false);
    }
  }

  function resetAndClose() {
    setFileQueue([]);
    setTitle('');
    setText('');
    setTags('');
    setScope('org');
    setScopeId('');
    setDragOver(false);
    onClose();
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start overflow-y-auto py-8 justify-center bg-black/50 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) resetAndClose(); }}
    >
      <div className="my-auto bg-bg-surface border border-border rounded-lg w-full max-w-[36rem] p-lg shadow-xl">
        <div className="flex items-center justify-between mb-lg">
          <h2 className="text-lg font-bold font-[family-name:var(--font-heading)] text-text-primary">
            Add Knowledge
          </h2>
          <button
            onClick={resetAndClose}
            className="text-text-muted hover:text-text-primary transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <Tabs
          tabs={[
            { id: 'file', label: 'Upload Files' },
            { id: 'directory', label: 'Upload Folder' },
            { id: 'text', label: 'Paste Text' },
          ]}
          active={tab}
          onChange={(t) => { setTab(t); setFileQueue([]); }}
        />

        <div className="mt-md space-y-md">
          <ScopeSelector
            scope={scope}
            scopeId={scopeId}
            onChange={(s, id) => { setScope(s); setScopeId(id); }}
          />

          <div>
            <label className="text-xs text-text-muted block mb-1">Tags</label>
            <input
              type="text"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="Comma-separated tags, e.g. finance, Q1, internal"
              className="w-full bg-bg-surface border border-border rounded px-2.5 py-2 text-sm text-text-primary focus:outline-none focus:border-primary"
            />
          </div>

          {/* ── Upload Files tab ── */}
          {tab === 'file' && (
            <>
              <div
                className={`border-2 border-dashed rounded-lg p-lg text-center cursor-pointer transition-colors ${
                  dragOver
                    ? 'border-primary bg-primary-light/20'
                    : 'border-border hover:border-primary/50'
                }`}
                onClick={() => fileRef.current?.click()}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files);
                }}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
              >
                <Upload size={24} className="mx-auto mb-2 text-text-muted" />
                <div className="text-sm text-text-secondary">
                  Drop files here or click to browse
                </div>
                <div className="text-[11px] text-text-muted mt-1">
                  PDF, TXT, Markdown, DOCX, CSV, code files
                </div>
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files && e.target.files.length > 0) addFiles(e.target.files);
                    e.target.value = '';
                  }}
                />
              </div>

              {fileQueue.length > 0 && (
                <div className="space-y-1 max-h-48 overflow-y-auto">
                  {fileQueue.map((q, i) => (
                    <div
                      key={`${q.file.name}-${q.file.size}`}
                      className="flex items-center gap-2 px-3 py-1.5 bg-bg-subtle rounded-md text-sm"
                    >
                      {q.status === 'uploading' ? <Loader2 size={14} className="shrink-0 text-primary animate-spin" />
                        : q.status === 'done' ? <CheckCircle2 size={14} className="shrink-0 text-success" />
                        : q.status === 'error' ? <AlertCircle size={14} className="shrink-0 text-error" />
                        : <FileText size={14} className="shrink-0 text-text-muted" />}
                      <span className="flex-1 truncate text-text-primary">{q.file.name}</span>
                      <span className="text-[11px] text-text-muted shrink-0">{formatSize(q.file.size)}</span>
                      {q.status === 'queued' && (
                        <button onClick={() => removeFile(i)} className="text-text-muted hover:text-error transition-colors shrink-0">
                          <X size={12} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <Button onClick={handleFileUpload} disabled={fileQueue.length === 0 || uploading} className="w-full">
                {uploading
                  ? `Uploading ${fileQueue.filter((q) => q.status === 'done').length}/${fileQueue.length}...`
                  : `Upload ${fileQueue.length || ''} File${fileQueue.length !== 1 ? 's' : ''}`}
              </Button>
            </>
          )}

          {/* ── Upload Folder tab ── */}
          {tab === 'directory' && (
            <>
              <div
                className="border-2 border-dashed border-border rounded-lg p-lg text-center cursor-pointer hover:border-primary/60 hover:bg-primary-light/30 transition-colors"
                onClick={() => dirRef.current?.click()}
              >
                <FolderOpen size={24} className="mx-auto mb-2 text-text-muted" />
                <div className="text-sm text-text-secondary">
                  {fileQueue.length > 0
                    ? `${fileQueue.length} file${fileQueue.length !== 1 ? 's' : ''} from folder`
                    : 'Click to select a folder'}
                </div>
                <div className="text-[11px] text-text-muted mt-1">
                  All supported files in the folder will be uploaded
                </div>
                <input
                  ref={dirRef}
                  type="file"
                  /* @ts-expect-error webkitdirectory is valid but not in TS types */
                  webkitdirectory=""
                  directory=""
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    if (e.target.files && e.target.files.length > 0) addFiles(e.target.files);
                    e.target.value = '';
                  }}
                />
              </div>

              {fileQueue.length > 0 && (
                <div className="space-y-0.5 max-h-48 overflow-y-auto">
                  {fileQueue.map((q, i) => (
                    <div
                      key={`${q.file.name}-${i}`}
                      className="flex items-center gap-2 px-3 py-1.5 bg-bg-subtle rounded-md text-xs"
                    >
                      {q.status === 'uploading' ? <Loader2 size={12} className="shrink-0 text-primary animate-spin" />
                        : q.status === 'done' ? <CheckCircle2 size={12} className="shrink-0 text-success" />
                        : q.status === 'error' ? <AlertCircle size={12} className="shrink-0 text-error" />
                        : <FileText size={12} className="shrink-0 text-text-muted" />}
                      <span className="truncate flex-1 text-text-primary">
                        {(q.file as File & { webkitRelativePath?: string }).webkitRelativePath || q.file.name}
                      </span>
                      <span className="text-[10px] text-text-muted shrink-0">{formatSize(q.file.size)}</span>
                      {q.status === 'queued' && !uploading && (
                        <button onClick={() => removeFile(i)} className="text-text-muted hover:text-error transition-colors shrink-0">
                          <X size={12} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <Button onClick={handleFileUpload} disabled={fileQueue.length === 0 || uploading} className="w-full">
                {uploading
                  ? `Uploading ${fileQueue.filter((q) => q.status === 'done').length}/${fileQueue.length}...`
                  : `Upload ${fileQueue.length} file${fileQueue.length !== 1 ? 's' : ''} from folder`}
              </Button>
            </>
          )}

          {/* ── Paste Text tab ── */}
          {tab === 'text' && (
            <>
              <FormField label="Title">
                <TextInput value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Document title" />
              </FormField>
              <FormField label="Content">
                <TextArea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  placeholder="Paste text content here..."
                  rows={8}
                />
              </FormField>
              <Button
                onClick={handleTextIngest}
                disabled={!title.trim() || !text.trim() || uploading}
                className="w-full"
              >
                {uploading ? 'Ingesting...' : 'Ingest Text'}
              </Button>
            </>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
