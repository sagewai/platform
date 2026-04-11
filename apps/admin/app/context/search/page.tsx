'use client';

import { useState } from 'react';
import { Card } from '@/components/ui/legacy';
import { Layers } from 'lucide-react';
import { adminApi } from '@/utils/api';
import type { ContextSearchResult } from '@/utils/types';
import { ContextSearchForm } from '@/components/context-search-form';
import { SearchResults } from '@/components/search-results';

export default function ContextSearchPage() {
  const [results, setResults] = useState<ContextSearchResult[]>([]);
  const [lastQuery, setLastQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);

  async function handleSearch(query: string, scopes: string[], topK: number) {
    setLoading(true);
    setLastQuery(query);
    try {
      const data = await adminApi.contextSearch({
        query,
        top_k: topK,
        scopes: scopes.length > 0 ? scopes : undefined,
      });
      setResults(data.results);
      setSearched(true);
    } catch {
      setResults([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-4xl mx-auto">
      <div className="mb-lg">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-1">Search</h1>
        <p className="text-text-muted text-sm">
          Multi-strategy semantic search across your knowledge base using vector similarity, BM25, and knowledge graph traversal with Reciprocal Rank Fusion.
        </p>
      </div>

      <Card className="p-md mb-lg">
        <div className="flex items-center gap-2 mb-3">
          <Layers size={14} className="text-primary" />
          <span className="text-xs font-medium">Search Strategy: Vector + BM25 + Graph (RRF Merge)</span>
        </div>
        <ContextSearchForm onSearch={handleSearch} loading={loading} />
      </Card>

      {searched && <SearchResults results={results} query={lastQuery} />}
    </div>
  );
}
