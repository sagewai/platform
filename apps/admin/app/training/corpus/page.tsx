'use client';

import { useEffect, useState } from 'react';
import { Card, Button, Skeleton, EmptyState } from '@sagecurator/ui';
import { Plus, FileText, Download, Trash2 } from 'lucide-react';

interface Corpus {
  id: string;
  name: string;
  version: number;
  examples: number;
  tokens: number;
  size_mb: number;
  created_at: string;
  status: 'ready' | 'processing' | 'error';
}

export default function CorpusPage() {
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setTimeout(() => { setCorpora([]); setLoading(false); }, 500);
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-text-secondary text-sm m-0">
          Build training datasets from agent run logs. Clean, deduplicate, and export.
        </p>
        <Button>
          <Plus size={14} className="mr-1.5" />
          New Corpus
        </Button>
      </div>

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-48 w-full rounded-xl" />)}
        </div>
      ) : corpora.length === 0 ? (
        <Card>
          <EmptyState
            title="No training corpora yet"
            description="Create a corpus by selecting conversations from Run Logs and building a dataset."
          />
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {corpora.map((c) => (
            <Card key={c.id}>
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <FileText size={18} className="text-primary" />
                  <h3 className="text-sm font-semibold m-0">{c.name}</h3>
                </div>
                <span className="text-xs text-text-muted">v{c.version}</span>
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs mb-4">
                <div><span className="text-text-muted">Examples:</span> <span className="font-medium">{c.examples.toLocaleString()}</span></div>
                <div><span className="text-text-muted">Tokens:</span> <span className="font-medium">{c.tokens.toLocaleString()}</span></div>
                <div><span className="text-text-muted">Size:</span> <span className="font-medium">{c.size_mb.toFixed(1)} MB</span></div>
                <div><span className="text-text-muted">Status:</span> <span className={`font-medium ${c.status === 'ready' ? 'text-success' : c.status === 'error' ? 'text-error' : 'text-warning'}`}>{c.status}</span></div>
              </div>
              <div className="flex gap-2">
                <Button className="flex-1"><Download size={12} className="mr-1" />Export</Button>
                <button className="p-2 text-text-muted hover:text-error transition-colors cursor-pointer bg-transparent border border-white/10 rounded-lg">
                  <Trash2 size={14} />
                </button>
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
