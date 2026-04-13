'use client';

import { useState } from 'react';
import { Button, TextInput } from '@/components/ui/legacy';
import { Search } from 'lucide-react';

interface Props {
  onSearch: (query: string, scopes: string[], topK: number) => void;
  loading?: boolean;
}

const ALL_SCOPES = ['org', 'project'] as const;

export function ContextSearchForm({ onSearch, loading }: Props) {
  const [query, setQuery] = useState('');
  const [selectedScopes, setSelectedScopes] = useState<string[]>([]);
  const [topK, setTopK] = useState(5);

  function toggleScope(scope: string) {
    setSelectedScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
    );
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    onSearch(query, selectedScopes.length > 0 ? selectedScopes : [], topK);
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-md">
      <div className="flex gap-2">
        <div className="flex-1">
          <TextInput
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search your knowledge base..."
          />
        </div>
        <Button type="submit" disabled={!query.trim() || loading}>
          <Search size={14} className="mr-1" />
          {loading ? 'Searching...' : 'Search'}
        </Button>
      </div>

      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">Scopes:</span>
          {ALL_SCOPES.map((scope) => {
            const active = selectedScopes.includes(scope);
            return (
              <button
                key={scope}
                type="button"
                onClick={() => toggleScope(scope)}
                className={`px-2.5 py-1 rounded text-xs font-medium border transition-colors ${
                  active
                    ? 'bg-primary/20 border-primary/50 text-primary'
                    : 'bg-bg-subtle border-border text-text-muted hover:border-text-muted'
                }`}
              >
                {scope}
              </button>
            );
          })}
          {selectedScopes.length > 0 && (
            <button
              type="button"
              onClick={() => setSelectedScopes([])}
              className="text-[11px] text-primary hover:underline"
            >
              All
            </button>
          )}
        </div>

        <div className="flex items-center gap-2 ml-auto">
          <span className="text-xs text-text-muted">Top-K:</span>
          <input
            type="range"
            min={1}
            max={20}
            value={topK}
            onChange={(e) => setTopK(parseInt(e.target.value))}
            className="w-24"
          />
          <span className="text-xs font-mono w-6 text-right">{topK}</span>
        </div>
      </div>
    </form>
  );
}
