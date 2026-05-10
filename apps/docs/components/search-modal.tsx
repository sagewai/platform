'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Search, X, ArrowRight, FileText } from 'lucide-react';
import Fuse from 'fuse.js';
import { SEARCH_INDEX, type SearchEntry } from '@/lib/search-index';

const fuse = new Fuse(SEARCH_INDEX, {
  keys: [
    { name: 'title', weight: 0.5 },
    { name: 'description', weight: 0.3 },
    { name: 'keywords', weight: 0.2 },
  ],
  threshold: 0.35,
  includeScore: true,
  minMatchCharLength: 2,
});

interface Props {
  open: boolean;
  onClose: () => void;
}

export function SearchModal({ open, onClose }: Props) {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchEntry[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);

  // Focus input when opened
  useEffect(() => {
    if (open) {
      setQuery('');
      setResults([]);
      setActiveIdx(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const handleQuery = useCallback((q: string) => {
    setQuery(q);
    setActiveIdx(0);
    if (!q.trim()) {
      setResults([]);
      return;
    }
    const hits = fuse.search(q).slice(0, 8);
    setResults(hits.map((h) => h.item));
  }, []);

  const navigate = useCallback(
    (entry: SearchEntry) => {
      router.push(entry.href);
      onClose();
    },
    [router, onClose],
  );

  const handleKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      } else if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIdx((i) => Math.min(i + 1, results.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIdx((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' && results[activeIdx]) {
        navigate(results[activeIdx]);
      }
    },
    [results, activeIdx, navigate, onClose],
  );

  // Scroll active item into view
  useEffect(() => {
    const item = listRef.current?.children[activeIdx] as HTMLElement | undefined;
    item?.scrollIntoView({ block: 'nearest' });
  }, [activeIdx]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-start justify-center pt-[12vh] px-4"
      role="dialog"
      aria-modal="true"
      aria-label="Search documentation"
    >
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Panel */}
      <div className="relative w-full max-w-xl bg-bg-surface border border-border rounded-xl shadow-2xl overflow-hidden">
        {/* Input row */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <Search size={18} className="text-text-muted shrink-0" />
          <input
            ref={inputRef}
            type="text"
            placeholder="Search docs…"
            value={query}
            onChange={(e) => handleQuery(e.target.value)}
            onKeyDown={handleKey}
            className="flex-1 bg-transparent text-text-primary placeholder:text-text-muted text-sm outline-none"
            aria-autocomplete="list"
            aria-controls="search-results"
            aria-activedescendant={results[activeIdx] ? `sr-${activeIdx}` : undefined}
          />
          {query && (
            <button
              onClick={() => handleQuery('')}
              className="text-text-muted hover:text-text-primary transition-colors"
              aria-label="Clear search"
            >
              <X size={16} />
            </button>
          )}
          <kbd className="hidden sm:inline-flex items-center gap-0.5 text-[11px] text-text-muted bg-bg-subtle border border-border rounded px-1.5 py-0.5 font-mono">
            Esc
          </kbd>
        </div>

        {/* Results */}
        {results.length > 0 ? (
          <ul
            id="search-results"
            ref={listRef}
            className="max-h-[min(60vh,360px)] overflow-y-auto py-2"
            role="listbox"
          >
            {results.map((entry, i) => (
              <li
                key={entry.href}
                id={`sr-${i}`}
                role="option"
                aria-selected={i === activeIdx}
              >
                <button
                  className={`w-full text-left px-4 py-2.5 flex items-start gap-3 transition-colors group ${
                    i === activeIdx ? 'bg-primary-light' : 'hover:bg-bg-subtle'
                  }`}
                  onClick={() => navigate(entry)}
                  onMouseEnter={() => setActiveIdx(i)}
                >
                  <FileText
                    size={16}
                    className={`mt-0.5 shrink-0 ${i === activeIdx ? 'text-primary' : 'text-text-muted group-hover:text-text-secondary'}`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span
                        className={`text-sm font-medium ${i === activeIdx ? 'text-primary' : 'text-text-primary'}`}
                      >
                        {entry.title}
                      </span>
                      <span className="text-[11px] text-text-muted bg-bg-subtle border border-border rounded px-1.5 py-0.5 leading-none">
                        {entry.section}
                      </span>
                    </div>
                    <p className="text-xs text-text-muted mt-0.5 truncate">{entry.description}</p>
                  </div>
                  <ArrowRight
                    size={14}
                    className={`mt-1 shrink-0 ${i === activeIdx ? 'text-primary' : 'text-text-muted opacity-0 group-hover:opacity-100'} transition-opacity`}
                  />
                </button>
              </li>
            ))}
          </ul>
        ) : query ? (
          <div className="px-4 py-8 text-center text-sm text-text-muted">
            No results for <span className="font-medium text-text-secondary">"{query}"</span>
          </div>
        ) : (
          <div className="px-4 py-6">
            <p className="text-xs font-semibold text-text-muted uppercase tracking-wider mb-3">
              Quick links
            </p>
            <div className="space-y-0.5">
              {SEARCH_INDEX.slice(0, 5).map((entry) => (
                <button
                  key={entry.href}
                  className="w-full text-left px-3 py-2 rounded-lg text-sm text-text-secondary hover:text-text-primary hover:bg-bg-subtle transition-colors flex items-center gap-2"
                  onClick={() => navigate(entry)}
                >
                  <FileText size={14} className="text-text-muted shrink-0" />
                  {entry.title}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="border-t border-border px-4 py-2 flex items-center gap-4 text-[11px] text-text-muted">
          <span className="flex items-center gap-1">
            <kbd className="font-mono bg-bg-subtle border border-border rounded px-1 py-0.5">↑↓</kbd>
            navigate
          </span>
          <span className="flex items-center gap-1">
            <kbd className="font-mono bg-bg-subtle border border-border rounded px-1 py-0.5">↵</kbd>
            open
          </span>
          <span className="flex items-center gap-1">
            <kbd className="font-mono bg-bg-subtle border border-border rounded px-1 py-0.5">Esc</kbd>
            close
          </span>
        </div>
      </div>
    </div>
  );
}
