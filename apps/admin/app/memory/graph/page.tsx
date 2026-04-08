'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { GraphStats, GraphEntity } from '@/utils/types';
import { StatCard } from '@/components/stat-card';
import { Card, Button, Skeleton } from '@sagecurator/ui';
import {
  Search, ArrowRight, ChevronRight, ChevronLeft, Network, X,
} from 'lucide-react';

const ENTITIES_PER_PAGE = 20;

const ENTITY_KIND_COLORS: Record<string, string> = {
  person: 'bg-blue-500/15 text-blue-400',
  organization: 'bg-purple-500/15 text-purple-400',
  concept: 'bg-teal-500/15 text-teal-400',
  location: 'bg-amber-500/15 text-amber-400',
  event: 'bg-rose-500/15 text-rose-400',
  default: 'bg-white/10 text-text-secondary',
};

function entityKindBadge(metadata: Record<string, unknown>) {
  const kind = (metadata?.kind as string) ?? (metadata?.type as string) ?? 'entity';
  const colorClass = ENTITY_KIND_COLORS[kind.toLowerCase()] ?? ENTITY_KIND_COLORS.default;
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-[10px] font-medium ${colorClass}`}>
      {kind}
    </span>
  );
}

export default function KnowledgeGraphPage() {
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Query
  const [query, setQuery] = useState('');
  const [queryResults, setQueryResults] = useState<{ content: string; rank: number }[]>([]);
  const [querying, setQuerying] = useState(false);

  // Entity browser
  const [entitySearch, setEntitySearch] = useState('');
  const [entities, setEntities] = useState<Array<{ name: string; metadata: Record<string, unknown> }>>([]);
  const [entityTotal, setEntityTotal] = useState(0);
  const [entityPage, setEntityPage] = useState(0);
  const [loadingEntities, setLoadingEntities] = useState(false);

  // Entity detail
  const [selectedEntity, setSelectedEntity] = useState<GraphEntity | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [relations, setRelations] = useState<Array<{ source: string; relation: string; target: string }>>([]);
  const [neighbors, setNeighbors] = useState<Array<{ entity: string; relation: string }>>([]);

  const fetchStats = useCallback(async () => {
    try {
      const data = await adminApi.getGraphStats();
      setStats(data);
      setError(null);
    } catch {
      setError('Failed to load graph stats. Is the admin backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchEntities = useCallback(async () => {
    setLoadingEntities(true);
    try {
      const data = await adminApi.graphListEntities({
        search: entitySearch || undefined,
        limit: ENTITIES_PER_PAGE,
        offset: entityPage * ENTITIES_PER_PAGE,
      });
      setEntities(data.entities);
      setEntityTotal(data.count);
    } catch {
      setEntities([]);
      setEntityTotal(0);
    } finally {
      setLoadingEntities(false);
    }
  }, [entitySearch, entityPage]);

  useEffect(() => { fetchStats(); }, [fetchStats]);
  useEffect(() => { fetchEntities(); }, [fetchEntities]);

  async function handleQuery(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setQuerying(true);
    setQueryResults([]);
    try {
      const data = await adminApi.graphQuery(query.trim());
      setQueryResults(data.results);
    } catch {
      setError('Graph query failed');
    } finally {
      setQuerying(false);
    }
  }

  async function selectEntity(name: string) {
    setLoadingDetail(true);
    try {
      const [entityData, relData, neighborData] = await Promise.all([
        adminApi.graphGetEntity(name),
        adminApi.graphGetRelations(name),
        adminApi.graphGetNeighbors(name),
      ]);
      setSelectedEntity(entityData);
      setRelations(relData.relations);
      setNeighbors(neighborData.neighbors);
    } catch {
      setError(`Failed to load entity "${name}"`);
    } finally {
      setLoadingDetail(false);
    }
  }

  function navigateToEntity(name: string) {
    selectEntity(name);
  }

  const entityTotalPages = Math.ceil(entityTotal / ENTITIES_PER_PAGE);

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)] text-text-primary">
        Knowledge Graph
      </h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Explore and manage the knowledge graph — entities, relations, and graph queries.
      </p>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md flex items-center justify-between">
          <span>{error}</span>
          <button onClick={() => setError(null)} className="text-error/60 hover:text-error">
            <X size={14} />
          </button>
        </div>
      )}

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-md mb-lg">
        {loading ? (
          <Skeleton lines={2} />
        ) : (
          <>
            <StatCard label="Status" value={stats?.status ?? 'unknown'} />
            <StatCard label="Entities" value={stats?.entities ?? 0} />
            <StatCard label="Relations" value={stats?.relations ?? 0} />
            <StatCard label="Backend" value={stats?.backend ?? '--'} />
          </>
        )}
      </div>

      {/* Graph Query */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)] text-text-primary">
          Graph Query
        </h3>
        <form onSubmit={handleQuery} className="flex gap-2 mb-md">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Enter graph query..."
            className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-bg-surface text-text-primary"
          />
          <Button type="submit" disabled={querying || !query.trim()}>
            {querying ? 'Querying...' : 'Query'}
          </Button>
        </form>
        {queryResults.length > 0 && (
          <div>
            {queryResults.map((r) => (
              <div key={r.rank} className="p-2.5 rounded-md border border-border mb-1.5 text-[13px] text-text-primary">
                <span className="text-text-muted mr-2">#{r.rank}</span>
                {r.content}
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* Two-column: Entity Browser + Entity Detail */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.5fr] gap-md">
        {/* Entity Browser */}
        <Card>
          <div className="flex items-center gap-2 mb-md">
            <Network size={16} className="text-text-muted" />
            <h3 className="mt-0 mb-0 text-base font-semibold font-[family-name:var(--font-heading)] text-text-primary">
              Entity Browser
            </h3>
          </div>

          {/* Search */}
          <div className="relative mb-md">
            <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-text-muted" />
            <input
              type="text"
              value={entitySearch}
              onChange={(e) => { setEntitySearch(e.target.value); setEntityPage(0); }}
              placeholder="Search entities..."
              className="w-full pl-8 pr-3 py-1.5 border border-border rounded-md text-sm bg-bg-surface text-text-primary focus:outline-none focus:border-primary"
            />
          </div>

          {/* Entity list */}
          {loadingEntities ? (
            <div className="space-y-2">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-10 bg-bg-subtle rounded animate-pulse" />
              ))}
            </div>
          ) : entities.length === 0 ? (
            <div className="text-sm text-text-muted text-center py-md">
              No entities found.
            </div>
          ) : (
            <div className="space-y-0.5">
              {entities.map((ent) => (
                <button
                  key={ent.name}
                  onClick={() => selectEntity(ent.name)}
                  className={`w-full flex items-center justify-between gap-2 px-2.5 py-2 rounded-md text-left text-sm transition-colors ${
                    selectedEntity?.name === ent.name
                      ? 'bg-primary-light/30 text-primary'
                      : 'hover:bg-bg-subtle text-text-primary'
                  }`}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="truncate">{ent.name}</span>
                    {entityKindBadge(ent.metadata)}
                  </div>
                  <ChevronRight size={14} className="text-text-muted shrink-0" />
                </button>
              ))}
            </div>
          )}

          {/* Pagination */}
          {entityTotalPages > 1 && (
            <div className="flex items-center justify-between mt-md text-xs text-text-muted">
              <span>{entityTotal} entities</span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setEntityPage((p) => Math.max(0, p - 1))}
                  disabled={entityPage === 0}
                  className="p-1 rounded hover:bg-bg-subtle disabled:opacity-30 transition-colors"
                >
                  <ChevronLeft size={14} />
                </button>
                <span className="px-1">
                  {entityPage + 1} / {entityTotalPages}
                </span>
                <button
                  onClick={() => setEntityPage((p) => Math.min(entityTotalPages - 1, p + 1))}
                  disabled={entityPage >= entityTotalPages - 1}
                  className="p-1 rounded hover:bg-bg-subtle disabled:opacity-30 transition-colors"
                >
                  <ChevronRight size={14} />
                </button>
              </div>
            </div>
          )}
        </Card>

        {/* Entity Detail */}
        <Card>
          {!selectedEntity && !loadingDetail ? (
            <div className="flex flex-col items-center justify-center py-xl text-text-muted">
              <Network size={32} className="mb-2 opacity-30" />
              <div className="text-sm">Select an entity to view details</div>
            </div>
          ) : loadingDetail ? (
            <div className="space-y-3 py-md">
              <Skeleton lines={3} />
            </div>
          ) : selectedEntity ? (
            <div>
              {/* Entity header */}
              <div className="flex items-start justify-between mb-md">
                <div>
                  <h3 className="mt-0 mb-1 text-lg font-semibold font-[family-name:var(--font-heading)] text-text-primary">
                    {selectedEntity.name}
                  </h3>
                  {entityKindBadge(selectedEntity.metadata)}
                </div>
                <button
                  onClick={() => { setSelectedEntity(null); setRelations([]); setNeighbors([]); }}
                  className="text-text-muted hover:text-text-primary transition-colors"
                >
                  <X size={16} />
                </button>
              </div>

              {/* Metadata */}
              {Object.keys(selectedEntity.metadata).length > 0 && (
                <div className="mb-md">
                  <h4 className="text-xs text-text-muted uppercase tracking-wide mb-1.5">Metadata</h4>
                  <div className="p-3 rounded-md border border-border bg-bg-subtle">
                    <pre className="m-0 text-xs text-text-secondary whitespace-pre-wrap break-words">
                      {JSON.stringify(selectedEntity.metadata, null, 2)}
                    </pre>
                  </div>
                </div>
              )}

              {/* Relations */}
              <div className="mb-md">
                <h4 className="text-xs text-text-muted uppercase tracking-wide mb-1.5">
                  Relations ({relations.length})
                </h4>
                {relations.length === 0 ? (
                  <div className="text-sm text-text-muted">No relations found.</div>
                ) : (
                  <div className="space-y-1">
                    {relations.map((rel, i) => (
                      <div
                        key={i}
                        className="flex items-center gap-2 px-2.5 py-1.5 rounded border border-border text-sm"
                      >
                        <button
                          onClick={() => navigateToEntity(rel.source)}
                          className={`font-medium hover:text-primary transition-colors truncate ${
                            rel.source === selectedEntity.name ? 'text-text-primary' : 'text-primary cursor-pointer'
                          }`}
                          disabled={rel.source === selectedEntity.name}
                        >
                          {rel.source}
                        </button>
                        <ArrowRight size={12} className="text-text-muted shrink-0" />
                        <span className="text-xs text-text-muted bg-bg-subtle px-1.5 py-0.5 rounded shrink-0">
                          {rel.relation}
                        </span>
                        <ArrowRight size={12} className="text-text-muted shrink-0" />
                        <button
                          onClick={() => navigateToEntity(rel.target)}
                          className={`font-medium hover:text-primary transition-colors truncate ${
                            rel.target === selectedEntity.name ? 'text-text-primary' : 'text-primary cursor-pointer'
                          }`}
                          disabled={rel.target === selectedEntity.name}
                        >
                          {rel.target}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Neighbors */}
              <div>
                <h4 className="text-xs text-text-muted uppercase tracking-wide mb-1.5">
                  Neighbors ({neighbors.length})
                </h4>
                {neighbors.length === 0 ? (
                  <div className="text-sm text-text-muted">No neighbors found.</div>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {neighbors.map((n, i) => (
                      <button
                        key={i}
                        onClick={() => navigateToEntity(n.entity)}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full border border-border text-xs text-text-secondary hover:border-primary hover:text-primary transition-colors bg-bg-surface"
                        title={`via: ${n.relation}`}
                      >
                        {n.entity}
                        <span className="text-[10px] text-text-muted">({n.relation})</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : null}
        </Card>
      </div>
    </div>
  );
}
