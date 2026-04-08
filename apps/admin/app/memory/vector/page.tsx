'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { VectorStats, VectorSearchResult } from '@/utils/types';
import { StatCard } from '@/components/stat-card';
import { Card, Button, Skeleton } from '@sagecurator/ui';
import { HelpPanel } from '@/components/help-panel';

export default function VectorStorePage() {
  const [stats, setStats] = useState<VectorStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Search
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<VectorSearchResult[]>([]);
  const [searching, setSearching] = useState(false);

  // Ingest
  const [ingestText, setIngestText] = useState('');
  const [ingesting, setIngesting] = useState(false);
  const [ingestMessage, setIngestMessage] = useState<string | null>(null);

  const fetchStats = useCallback(async () => {
    try {
      const data = await adminApi.getVectorStats();
      setStats(data);
      setError(null);
    } catch {
      setError('Failed to load vector store stats. Is the admin backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
  }, [fetchStats]);

  async function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setResults([]);
    try {
      const data = await adminApi.vectorSearch(query.trim());
      setResults(data.results);
    } catch {
      setError('Search failed');
    } finally {
      setSearching(false);
    }
  }

  async function handleIngest(e: React.FormEvent) {
    e.preventDefault();
    if (!ingestText.trim()) return;
    setIngesting(true);
    setIngestMessage(null);
    try {
      await adminApi.vectorIngest(ingestText.trim());
      setIngestMessage('Document ingested successfully');
      setIngestText('');
      fetchStats();
    } catch {
      setError('Ingestion failed');
    } finally {
      setIngesting(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Vector Store</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Manage the vector memory store — view stats, search documents, and ingest new content.
      </p>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-md mb-lg">
        {loading ? (
          <Skeleton lines={2} />
        ) : (
          <>
            <StatCard label="Status" value={stats?.status ?? 'unknown'} />
            <StatCard label="Documents" value={stats?.documents ?? 0} />
            <StatCard label="Backend" value={stats?.backend ?? '--'} />
          </>
        )}
      </div>

      {/* Search */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Semantic Search</h3>
        <form onSubmit={handleSearch} className="flex gap-2 mb-md">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Enter search query..."
            className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
          />
          <Button
            type="submit"
            disabled={searching || !query.trim()}
          >
            {searching ? 'Searching...' : 'Search'}
          </Button>
        </form>

        {results.length > 0 && (
          <div>
            <p className="text-[13px] text-text-muted mb-2">
              {results.length} result{results.length !== 1 ? 's' : ''} found
            </p>
            {results.map((r) => (
              <div
                key={r.rank}
                className="p-3 rounded-md border border-border mb-2"
              >
                <div className="text-xs text-text-muted mb-1">Rank #{r.rank}</div>
                <div className="text-sm whitespace-pre-wrap">{r.content}</div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Ingest */}
      <Card>
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Ingest Document</h3>
        <form onSubmit={handleIngest}>
          <textarea
            value={ingestText}
            onChange={(e) => setIngestText(e.target.value)}
            placeholder="Paste text content to ingest into the vector store..."
            rows={5}
            className="w-full px-3 py-2.5 border border-border rounded-md text-sm resize-y box-border bg-bg-surface"
          />
          <div className="flex items-center gap-3 mt-3">
            <Button
              type="submit"
              variant="secondary"
              disabled={ingesting || !ingestText.trim()}
            >
              {ingesting ? 'Ingesting...' : 'Ingest'}
            </Button>
            {ingestMessage && (
              <span className="text-[13px] text-success">{ingestMessage}</span>
            )}
          </div>
        </form>
      </Card>

      <HelpPanel title="Vector Store">
        <h3>What is the Vector Store?</h3>
        <p>The vector store holds document embeddings for semantic search and RAG (Retrieval-Augmented Generation). Agents use it to ground responses in your data.</p>
        <h3>Ingest Formats</h3>
        <ul>
          <li><strong>Plain text</strong> — paste any text content directly</li>
          <li><strong>Metadata</strong> — attach JSON metadata for filtering</li>
        </ul>
        <h3>Semantic Search</h3>
        <p>Search uses cosine similarity to find the most relevant documents. Results are ranked by relevance score.</p>
        <h3>Backend</h3>
        <p>Uses Milvus for production or an in-memory store for local development. Configure via <code>MILVUS_URI</code> environment variable.</p>
      </HelpPanel>
    </div>
  );
}
