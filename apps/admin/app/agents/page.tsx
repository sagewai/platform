'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { AgentSummary } from '@/utils/types';
import { Button } from '@sagecurator/ui';
import { Search, ChevronUp, ChevronDown, ChevronLeft, ChevronRight, X, Tag, AlertTriangle } from 'lucide-react';

type SortField = 'name' | 'model' | 'strategy' | 'status';
type SortDir = 'asc' | 'desc';

const PAGE_SIZE = 20;

export default function AgentsPage() {
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [apiError, setApiError] = useState(false);

  // Search & filter state
  const [search, setSearch] = useState('');
  const [sourceFilter, setSourceFilter] = useState<'all' | 'registered' | 'playground'>('all');
  const [modelFilter, setModelFilter] = useState('');
  const [tagFilter, setTagFilter] = useState('');
  const [sortField, setSortField] = useState<SortField>('name');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [page, setPage] = useState(0);

  useEffect(() => {
    adminApi.listAgents()
      .then(setAgents)
      .catch(() => setApiError(true))
      .finally(() => setLoading(false));
  }, []);

  // Unique models for the dropdown
  const uniqueModels = useMemo(() => {
    const models = new Set(agents.map((a) => a.model).filter(Boolean));
    return Array.from(models).sort();
  }, [agents]);

  // Unique tags across all agents
  const allTags = useMemo(() => {
    const tags = new Set(agents.flatMap((a) => a.tags ?? []));
    return Array.from(tags).sort();
  }, [agents]);

  // Filter + search
  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return agents.filter((a) => {
      if (sourceFilter !== 'all' && a.source !== sourceFilter) return false;
      if (modelFilter && a.model !== modelFilter) return false;
      if (tagFilter && !(a.tags ?? []).includes(tagFilter)) return false;
      if (q) {
        const searchable = [
          a.name,
          a.model,
          a.strategy,
          ...(a.capabilities || []),
          ...(a.tags ?? []),
        ].join(' ').toLowerCase();
        if (!searchable.includes(q)) return false;
      }
      return true;
    });
  }, [agents, search, sourceFilter, modelFilter, tagFilter]);

  // Sort
  const sorted = useMemo(() => {
    const arr = [...filtered];
    arr.sort((a, b) => {
      const av = (a[sortField] ?? '').toLowerCase();
      const bv = (b[sortField] ?? '').toLowerCase();
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === 'asc' ? cmp : -cmp;
    });
    return arr;
  }, [filtered, sortField, sortDir]);

  // Paginate
  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const paginated = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

  // Reset page when filters change
  useEffect(() => { setPage(0); }, [search, sourceFilter, modelFilter, tagFilter]);

  function toggleSort(field: SortField) {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir('asc');
    }
  }

  function SortIcon({ field }: { field: SortField }) {
    if (sortField !== field) return <ChevronUp size={10} className="opacity-0 group-hover:opacity-30" />;
    return sortDir === 'asc' ? <ChevronUp size={10} /> : <ChevronDown size={10} />;
  }

  const hasFilters = search || sourceFilter !== 'all' || modelFilter || tagFilter;

  if (loading) {
    return (
      <div className="max-w-6xl mx-auto">
        <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Agent Registry</h1>
        <div className="animate-pulse space-y-md mt-lg">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-12 bg-bg-subtle rounded-lg" />
          ))}
        </div>
      </div>
    );
  }

  if (apiError) {
    return (
      <div className="max-w-6xl mx-auto">
        <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Agent Registry</h1>
        <div className="flex items-center gap-3 bg-error/5 border border-error/20 rounded-lg px-4 py-3 mt-lg">
          <AlertTriangle className="w-4 h-4 text-error flex-shrink-0" />
          <div>
            <p className="text-sm font-medium text-error m-0">Failed to load agents</p>
            <p className="text-xs text-text-muted m-0 mt-0.5">
              The API server is not responding. Check that the backend is running and try again.
            </p>
          </div>
          <Button variant="ghost" size="sm" onClick={() => { setApiError(false); setLoading(true); adminApi.listAgents().then(setAgents).catch(() => setApiError(true)).finally(() => setLoading(false)); }} className="ml-auto">
            Retry
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto">
      <div className="flex items-start justify-between mb-md">
        <div>
          <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Agent Registry</h1>
          <p className="mt-0 text-sm text-text-secondary">
            {agents.length} agent{agents.length !== 1 ? 's' : ''} registered
            {filtered.length !== agents.length && ` (${filtered.length} matching)`}
          </p>
        </div>
        <Link href="/playground">
          <Button variant="secondary">Create in Playground</Button>
        </Link>
      </div>

      {/* Search & Filters */}
      <div className="flex flex-wrap gap-2 mb-md">
        {/* Search */}
        <div className="relative flex-1 min-w-[200px]">
          <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by name, model, tags, capabilities..."
            className="w-full pl-8 pr-3 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface outline-none focus:border-primary transition-colors"
          />
          {search && (
            <button
              type="button"
              onClick={() => setSearch('')}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary bg-transparent border-none cursor-pointer p-0.5"
            >
              <X size={12} />
            </button>
          )}
        </div>

        {/* Source filter */}
        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value as typeof sourceFilter)}
          className="px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface"
        >
          <option value="all">All sources</option>
          <option value="registered">Registered</option>
          <option value="playground">Playground</option>
        </select>

        {/* Model filter */}
        <select
          value={modelFilter}
          onChange={(e) => setModelFilter(e.target.value)}
          className="px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface max-w-[200px]"
        >
          <option value="">All models</option>
          {uniqueModels.map((m) => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>

        {/* Tag filter */}
        {allTags.length > 0 && (
          <select
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value)}
            className="px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-bg-surface"
          >
            <option value="">All tags</option>
            {allTags.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        )}

        {hasFilters && (
          <button
            type="button"
            onClick={() => { setSearch(''); setSourceFilter('all'); setModelFilter(''); setTagFilter(''); }}
            className="px-2.5 py-[7px] rounded-md border border-border text-[13px] bg-transparent cursor-pointer hover:bg-bg-subtle text-text-muted transition-colors"
          >
            Clear filters
          </button>
        )}
      </div>

      {/* Table */}
      {sorted.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-2xl text-center">
          {agents.length === 0 ? (
            <>
              <h3 className="text-lg font-semibold text-text-primary mb-1 font-[family-name:var(--font-heading)]">No Agents</h3>
              <p className="text-sm text-text-secondary max-w-[28rem]">
                No agents registered. Start your backend or{' '}
                <Link href="/playground" className="text-primary no-underline hover:underline">create one in the Playground</Link>.
              </p>
            </>
          ) : (
            <>
              <h3 className="text-lg font-semibold text-text-primary mb-1">No matches</h3>
              <p className="text-sm text-text-secondary">No agents match your filters. Try broadening your search.</p>
            </>
          )}
        </div>
      ) : (
        <>
          <div className="bg-bg-surface rounded-lg border border-border overflow-hidden">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b border-border">
                  <SortableHeader field="name" label="Name" sortField={sortField} sortDir={sortDir} onToggle={toggleSort} />
                  <SortableHeader field="model" label="Model" sortField={sortField} sortDir={sortDir} onToggle={toggleSort} />
                  <SortableHeader field="strategy" label="Strategy" sortField={sortField} sortDir={sortDir} onToggle={toggleSort} className="hidden md:table-cell" />
                  <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide hidden lg:table-cell">Tags</th>
                  <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide hidden lg:table-cell">Capabilities</th>
                  <SortableHeader field="status" label="Status" sortField={sortField} sortDir={sortDir} onToggle={toggleSort} />
                </tr>
              </thead>
              <tbody>
                {paginated.map((agent) => (
                  <tr key={agent.name} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
                    <td className="py-3 px-4">
                      <Link href={`/agents/${encodeURIComponent(agent.name)}`} className="font-semibold text-primary no-underline hover:underline">
                        {agent.name}
                      </Link>
                      {agent.source === 'playground' && (
                        <span className="ml-1.5 text-[9px] font-semibold uppercase tracking-wider bg-secondary/15 text-secondary px-1 py-0.5 rounded align-middle">
                          pg
                        </span>
                      )}
                    </td>
                    <td className="py-3 px-4">
                      <span className="font-[family-name:var(--font-mono)] text-xs bg-bg-subtle px-2 py-0.5 rounded truncate inline-block max-w-[180px]" title={agent.model}>
                        {agent.model || '\u2014'}
                      </span>
                    </td>
                    <td className="py-3 px-4 hidden md:table-cell">
                      {agent.strategy ? (
                        <span className="text-xs bg-bg-subtle px-2 py-0.5 rounded font-[family-name:var(--font-mono)]">{agent.strategy}</span>
                      ) : (
                        <span className="text-text-muted">&mdash;</span>
                      )}
                    </td>
                    <td className="py-3 px-4 hidden lg:table-cell">
                      <div className="flex flex-wrap gap-1">
                        {(agent.tags ?? []).map((tag) => (
                          <button
                            key={tag}
                            type="button"
                            onClick={() => setTagFilter(tag)}
                            className="inline-flex items-center gap-0.5 text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded-full font-medium border-none cursor-pointer hover:bg-primary/20 transition-colors"
                          >
                            <Tag size={8} />
                            {tag}
                          </button>
                        ))}
                      </div>
                    </td>
                    <td className="py-3 px-4 hidden lg:table-cell">
                      <div className="flex flex-wrap gap-1">
                        {agent.capabilities.slice(0, 3).map((cap) => (
                          <span key={cap} className="text-[10px] bg-bg-subtle text-text-secondary px-1.5 py-0.5 rounded-full font-[family-name:var(--font-mono)]">
                            {cap}
                          </span>
                        ))}
                        {agent.capabilities.length > 3 && (
                          <span className="text-[10px] text-text-muted">+{agent.capabilities.length - 3}</span>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4">
                      <span className={`inline-flex items-center gap-1.5 text-sm ${
                        agent.status === 'active' ? 'text-success' : 'text-text-muted'
                      }`}>
                        <span className={`w-2 h-2 rounded-full ${
                          agent.status === 'active' ? 'bg-success' : 'bg-text-muted'
                        }`} />
                        {agent.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="flex items-center justify-between mt-md">
              <span className="text-xs text-text-muted">
                Showing {page * PAGE_SIZE + 1}\u2013{Math.min((page + 1) * PAGE_SIZE, sorted.length)} of {sorted.length}
              </span>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="p-1.5 rounded border border-border bg-transparent cursor-pointer hover:bg-bg-subtle disabled:opacity-30 disabled:cursor-default text-text-muted transition-colors"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="text-xs text-text-secondary px-2">
                  Page {page + 1} of {totalPages}
                </span>
                <button
                  type="button"
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                  className="p-1.5 rounded border border-border bg-transparent cursor-pointer hover:bg-bg-subtle disabled:opacity-30 disabled:cursor-default text-text-muted transition-colors"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SortableHeader({
  field,
  label,
  sortField,
  sortDir,
  onToggle,
  className = '',
}: {
  field: SortField;
  label: string;
  sortField: SortField;
  sortDir: SortDir;
  onToggle: (f: SortField) => void;
  className?: string;
}) {
  const active = sortField === field;
  return (
    <th className={`text-left py-3 px-4 text-xs font-semibold uppercase tracking-wide ${className}`}>
      <button
        type="button"
        onClick={() => onToggle(field)}
        className={`group inline-flex items-center gap-1 border-none bg-transparent cursor-pointer p-0 font-semibold text-xs uppercase tracking-wide transition-colors ${
          active ? 'text-primary' : 'text-text-muted hover:text-text-secondary'
        }`}
      >
        {label}
        {active ? (
          sortDir === 'asc' ? <ChevronUp size={10} /> : <ChevronDown size={10} />
        ) : (
          <ChevronUp size={10} className="opacity-0 group-hover:opacity-30" />
        )}
      </button>
    </th>
  );
}
