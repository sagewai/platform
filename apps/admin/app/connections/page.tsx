'use client';

// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
//
// PR5 rewrite: unified single-page filterable list. Replaces the 3-tab
// implementation from PR #356.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useToast } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type {
  BackendMeta,
  Connection,
  CredentialsBackendKind,
  ProtocolMeta,
} from '@/utils/connection-types';
import { AddConnectionModal } from '@/components/connections/add-connection-modal';
import { ConnectionsTable } from '@/components/connections/connections-table';
import { DetailDrawer } from '@/components/connections/detail-drawer';
import { ExportDropdown } from '@/components/connections/export-dropdown';
import { FilterBar } from '@/components/connections/filter-bar';
import { ImportModal } from '@/components/connections/import-modal';

export default function ConnectionsPage() {
  const { currentSlug } = useProject();
  // useToast returns a fresh object each render — pin it through a ref so
  // it doesn't churn callback identity (and re-fire the load effect).
  const { toast } = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;

  const [protocols, setProtocols] = useState<ProtocolMeta[]>([]);
  const [backends, setBackends] = useState<BackendMeta[]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [loading, setLoading] = useState(true);

  const [filterProtocol, setFilterProtocol] = useState<string | null>(null);
  const [filterTags, setFilterTags] = useState<string[]>([]);
  const [search, setSearch] = useState('');

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [showImportModal, setShowImportModal] = useState(false);

  // Default backend for the modal — for PR5 kickoff, hardcode "local".
  // A future enhancement can read this from a platform-config endpoint.
  const defaultBackend: CredentialsBackendKind = 'local';

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [pls, bks, cns] = await Promise.all([
        adminApi.connections.protocols(),
        adminApi.connections.backends(),
        adminApi.connections.list({
          protocol: filterProtocol ?? undefined,
          // server filters by single tag; multi-tag AND happens client-side
          tag: filterTags[0] ?? undefined,
        }),
      ]);
      setProtocols(pls);
      setBackends(bks);
      setConnections(cns);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastRef.current('error', `Failed to load connections: ${msg}`);
    } finally {
      setLoading(false);
    }
  }, [filterProtocol, filterTags]);

  useEffect(() => {
    void reload();
  }, [reload, currentSlug]);

  const protocolNames = useMemo(
    () => Object.fromEntries(protocols.map(p => [p.id, p.display_name])),
    [protocols],
  );

  const knownTags = useMemo(() => {
    const all = new Set<string>();
    connections.forEach(c => c.tags.forEach(t => all.add(t)));
    return Array.from(all).sort();
  }, [connections]);

  // Apply additional filters client-side (multi-tag AND + search).
  const filteredConnections = useMemo(() => {
    return connections.filter(c => {
      // Multi-tag AND
      if (filterTags.length > 0 && !filterTags.every(t => c.tags.includes(t))) {
        return false;
      }
      // Search (display_name, id, tags substring)
      if (search) {
        const needle = search.toLowerCase();
        const hay = `${c.display_name} ${c.id} ${c.tags.join(' ')}`.toLowerCase();
        if (!hay.includes(needle)) return false;
      }
      return true;
    });
  }, [connections, filterTags, search]);

  const selected = useMemo(
    () => filteredConnections.find(c => c.id === selectedId) ?? null,
    [filteredConnections, selectedId],
  );

  const handleSetDefault = async (c: Connection) => {
    try {
      await adminApi.connections.setDefault(c.id);
      toastRef.current('success', `${c.display_name} is now the default`);
      await reload();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastRef.current('error', `Failed to set default: ${msg}`);
    }
  };

  const handleAction = async (
    c: Connection,
    action: 'test' | 'delete' | 'authorize' | 'refresh' | 'revoke',
  ) => {
    try {
      if (action === 'test') {
        const r = await adminApi.connections.test(c.id);
        toastRef.current(
          r.ok ? 'success' : 'error',
          `${c.display_name}: ${r.message ?? (r.ok ? 'ok' : 'failed')}`,
        );
      } else if (action === 'delete') {
        if (!confirm(`Delete ${c.display_name}? This cannot be undone.`)) return;
        await adminApi.connections.delete(c.id);
        toastRef.current('success', `${c.display_name} deleted`);
      } else if (action === 'authorize') {
        const startRes = await adminApi.connections.oauth2.start(c.id);
        window.open(startRes.authorize_url, '_blank', 'width=600,height=800');
        toastRef.current('success', 'Authorize the connection in the popup');
      } else if (action === 'refresh') {
        await adminApi.connections.oauth2.refresh(c.id);
        toastRef.current('success', `${c.display_name} tokens refreshed`);
      } else if (action === 'revoke') {
        if (!confirm(`Revoke ${c.display_name}? Tokens will be cleared.`)) return;
        await adminApi.connections.oauth2.revoke(c.id);
        toastRef.current('success', `${c.display_name} revoked`);
      }
      await reload();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toastRef.current('error', `Action failed: ${msg}`);
    }
  };

  return (
    <div className="mx-auto max-w-7xl p-6" data-testid="connections-page">
      <header className="mb-4 flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Connections</h1>
          <p className="mt-1 text-sm text-text-secondary">
            External dependencies — LLM providers, OAuth clients, MCP servers, HTTP tools.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ExportDropdown
            projectId={currentSlug ?? 'default'}
            availableProtocols={protocols}
          />
          <button
            type="button"
            onClick={() => setShowImportModal(true)}
            className="rounded border border-border px-3 py-1.5 text-sm hover:bg-bg-secondary"
            data-testid="import-yaml-button"
          >
            Import YAML
          </button>
        </div>
      </header>

      <FilterBar
        protocols={protocols}
        selectedProtocol={filterProtocol}
        onSelectProtocol={setFilterProtocol}
        selectedTags={filterTags}
        knownTags={knownTags}
        onChangeTags={setFilterTags}
        search={search}
        onChangeSearch={setSearch}
        onAddConnection={() => setShowAddModal(true)}
      />

      {loading ? (
        <p className="text-sm text-text-tertiary" data-testid="connections-loading">
          Loading...
        </p>
      ) : (
        <ConnectionsTable
          connections={filteredConnections}
          protocolNames={protocolNames}
          onRowClick={c => setSelectedId(c.id)}
          onSetDefault={handleSetDefault}
          onAction={handleAction}
        />
      )}

      <DetailDrawer
        connection={selected}
        protocolNames={protocolNames}
        onClose={() => setSelectedId(null)}
        onRefresh={reload}
      />

      <AddConnectionModal
        open={showAddModal}
        protocols={protocols}
        backends={backends}
        defaultBackend={defaultBackend}
        onClose={() => setShowAddModal(false)}
        onAuthorized={() => { setShowAddModal(false); void reload(); }}
      />

      <ImportModal
        projectId={currentSlug ?? 'default'}
        open={showImportModal}
        onClose={() => setShowImportModal(false)}
        onImported={() => {
          setShowImportModal(false);
          void reload();
        }}
      />
    </div>
  );
}
