'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Skeleton,
  useToast,
} from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type {
  OAuthClient,
  OAuthClientStatus,
  OAuthProviderMeta,
} from '@/utils/types';
import {
  Plus,
  RefreshCw,
  Star,
  Trash2,
  MoreHorizontal,
  ShieldCheck,
  ShieldX,
  KeyRound,
} from 'lucide-react';
import { AddOAuthClientModal } from './add-oauth-client-modal';

export function OAuthTab() {
  const [providers, setProviders] = useState<OAuthProviderMeta[]>([]);
  const [clients, setClients] = useState<OAuthClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<
    { id: string; action: 'revoke' | 'delete'; label: string } | null
  >(null);
  const { toast } = useToast();

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [provs, list] = await Promise.all([
        adminApi.oauthClients.providers(),
        adminApi.oauthClients.list(),
      ]);
      setProviders(provs);
      setClients(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  function providerLabel(id: string): string {
    return providers.find((p) => p.id === id)?.display_name ?? id;
  }

  async function handleSetDefault(id: string, label: string) {
    setBusyId(id);
    try {
      await adminApi.oauthClients.setDefault(id);
      toast('success', `${label} set as default for this project.`);
      await refresh();
    } catch (e) {
      toast('error', `Set default failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  }

  async function handleAuthorize(id: string) {
    setBusyId(id);
    try {
      const resp = await adminApi.oauthClients.start(id);
      if (typeof window !== 'undefined') {
        window.open(resp.authorize_url, '_blank', 'width=600,height=800');
      }
      toast(
        'info',
        'Opened the provider authorization window. Refresh the list once you complete it.',
      );
    } catch (e) {
      toast('error', `Authorize failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  }

  async function handleRefresh(id: string, label: string) {
    setBusyId(id);
    try {
      await adminApi.oauthClients.refresh(id);
      toast('success', `${label} tokens refreshed.`);
      await refresh();
    } catch (e) {
      toast('error', `Refresh failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
    }
  }

  async function handleRevoke(id: string, label: string) {
    setBusyId(id);
    try {
      await adminApi.oauthClients.revoke(id);
      toast('success', `${label} tokens revoked.`);
      await refresh();
    } catch (e) {
      toast('error', `Revoke failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
      setConfirm(null);
    }
  }

  async function handleDelete(id: string, label: string) {
    setBusyId(id);
    try {
      await adminApi.oauthClients.delete(id);
      toast('success', `${label} removed.`);
      await refresh();
    } catch (e) {
      toast('error', `Delete failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusyId(null);
      setConfirm(null);
    }
  }

  if (loading) {
    return (
      <div className="mt-4 space-y-3">
        <Skeleton lines={4} />
        <Skeleton lines={4} />
      </div>
    );
  }

  if (error) {
    return (
      <EmptyState
        title="Could not load OAuth clients"
        description={error}
        actionLabel="Retry"
        onAction={() => {
          setLoading(true);
          refresh().finally(() => setLoading(false));
        }}
      />
    );
  }

  return (
    <div className="mt-4 space-y-4" data-testid="oauth-tab">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold m-0">OAuth Clients</h2>
          <p className="text-xs text-muted-foreground mt-0.5">
            {providers.length} provider{providers.length === 1 ? '' : 's'} available
            {clients.length > 0 && ` · ${clients.length} client${clients.length === 1 ? '' : 's'} configured`}
          </p>
        </div>
        <Button
          size="sm"
          onClick={() => setShowAddModal(true)}
          disabled={providers.length === 0}
          data-testid="oauth-add-button"
        >
          <Plus className="w-3.5 h-3.5 mr-1.5" />
          Add OAuth Client
        </Button>
      </div>

      {clients.length === 0 ? (
        <div
          data-testid="oauth-clients-empty"
          className="rounded-md border border-dashed p-12 text-center text-sm text-muted-foreground"
        >
          <h3 className="text-base font-medium text-foreground">No OAuth clients yet</h3>
          <p className="mt-2">
            Click <em>Add OAuth Client</em> to register a credential for any of the{' '}
            {providers.length} available provider{providers.length === 1 ? '' : 's'}.
          </p>
        </div>
      ) : (
        <div
          data-testid="oauth-clients-list"
          className="rounded-lg border border-border overflow-hidden"
        >
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs text-muted-foreground">
              <tr>
                <Th>Provider</Th>
                <Th>Display name</Th>
                <Th>Status</Th>
                <Th>Scopes</Th>
                <Th>Default</Th>
                <Th>Last refreshed</Th>
                <Th className="text-right pr-4">Actions</Th>
              </tr>
            </thead>
            <tbody>
              {clients.map((c) => (
                <OAuthClientRow
                  key={c.id}
                  client={c}
                  providerLabel={providerLabel(c.provider)}
                  busy={busyId === c.id}
                  onSetDefault={() => handleSetDefault(c.id, c.display_name)}
                  onAuthorize={() => handleAuthorize(c.id)}
                  onRefresh={() => handleRefresh(c.id, c.display_name)}
                  onRevoke={() =>
                    setConfirm({ id: c.id, action: 'revoke', label: c.display_name })
                  }
                  onDelete={() =>
                    setConfirm({ id: c.id, action: 'delete', label: c.display_name })
                  }
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showAddModal && (
        <AddOAuthClientModal
          onClose={() => setShowAddModal(false)}
          onAuthorized={async () => {
            // Don't auto-close — modal stays open showing the authorized
            // state until the operator dismisses it; refresh the list so
            // the row appears underneath when they do.
            await refresh();
          }}
        />
      )}

      {confirm && confirm.action === 'revoke' && (
        <ConfirmDialog
          open
          onClose={() => setConfirm(null)}
          onConfirm={() => handleRevoke(confirm.id, confirm.label)}
          title={`Revoke ${confirm.label}?`}
          message="This clears the stored access + refresh tokens. The client record stays so you can re-authorize without re-entering the client_id/secret."
          confirmLabel="Revoke tokens"
        />
      )}

      {confirm && confirm.action === 'delete' && (
        <ConfirmDialog
          open
          onClose={() => setConfirm(null)}
          onConfirm={() => handleDelete(confirm.id, confirm.label)}
          title={`Delete ${confirm.label}?`}
          message="This permanently removes the OAuth client and its tokens. Other projects retain their own clients."
          confirmLabel="Delete client"
        />
      )}
    </div>
  );
}

// ── Row ─────────────────────────────────────────────────────────────────────

function OAuthClientRow({
  client,
  providerLabel,
  busy,
  onSetDefault,
  onAuthorize,
  onRefresh,
  onRevoke,
  onDelete,
}: {
  client: OAuthClient;
  providerLabel: string;
  busy: boolean;
  onSetDefault: () => void;
  onAuthorize: () => void;
  onRefresh: () => void;
  onRevoke: () => void;
  onDelete: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const lastRefreshed =
    client.tokens?.last_refreshed_at ??
    client.tokens?.obtained_at ??
    null;

  return (
    <tr className="border-t border-border" data-testid={`oauth-row-${client.id}`}>
      <Td>{providerLabel}</Td>
      <Td>
        <span className="font-medium">{client.display_name}</span>
      </Td>
      <Td>
        <StatusPill status={client.status} />
      </Td>
      <Td>
        <ScopesCell scopes={client.granted_scopes.length > 0 ? client.granted_scopes : client.requested_scopes} />
      </Td>
      <Td>
        <button
          aria-label={client.is_default ? 'Default' : 'Set as default'}
          onClick={onSetDefault}
          disabled={busy || client.is_default}
          className={`p-1 rounded transition-colors ${
            client.is_default
              ? 'text-amber-500'
              : 'text-muted-foreground hover:text-foreground'
          } disabled:opacity-50`}
          data-testid={`oauth-default-${client.id}`}
        >
          <Star
            className="w-4 h-4"
            fill={client.is_default ? 'currentColor' : 'none'}
          />
        </button>
      </Td>
      <Td>
        <span className="text-xs text-muted-foreground">
          {lastRefreshed ? formatRelative(lastRefreshed) : '—'}
        </span>
      </Td>
      <Td className="text-right pr-4 relative">
        <button
          aria-label="Actions"
          className="p-1 rounded hover:bg-muted disabled:opacity-50"
          onClick={() => setMenuOpen((o) => !o)}
          disabled={busy}
          data-testid={`oauth-actions-${client.id}`}
        >
          <MoreHorizontal className="w-4 h-4" />
        </button>
        {menuOpen && (
          <ActionsMenu
            client={client}
            onClose={() => setMenuOpen(false)}
            onAuthorize={onAuthorize}
            onRefresh={onRefresh}
            onRevoke={onRevoke}
            onDelete={onDelete}
          />
        )}
      </Td>
    </tr>
  );
}

// ── Actions menu ────────────────────────────────────────────────────────────

function ActionsMenu({
  client,
  onClose,
  onAuthorize,
  onRefresh,
  onRevoke,
  onDelete,
}: {
  client: OAuthClient;
  onClose: () => void;
  onAuthorize: () => void;
  onRefresh: () => void;
  onRevoke: () => void;
  onDelete: () => void;
}) {
  const isPending = client.status === 'pending';
  const isAuthorized = client.status === 'authorized';

  return (
    <div
      role="menu"
      className="absolute right-3 top-9 z-20 w-48 rounded-md border border-border bg-popover shadow-md py-1 text-left text-sm"
      onMouseLeave={onClose}
    >
      <MenuItem
        icon={<KeyRound className="w-3.5 h-3.5" />}
        label={isPending ? 'Authorize' : 'Re-authorize'}
        onClick={() => {
          onAuthorize();
          onClose();
        }}
      />
      {isAuthorized && (
        <MenuItem
          icon={<RefreshCw className="w-3.5 h-3.5" />}
          label="Refresh now"
          onClick={() => {
            onRefresh();
            onClose();
          }}
        />
      )}
      <MenuItem
        icon={<ShieldX className="w-3.5 h-3.5" />}
        label="Revoke"
        onClick={() => {
          onRevoke();
          onClose();
        }}
      />
      <MenuItem
        icon={<Trash2 className="w-3.5 h-3.5" />}
        label="Delete"
        destructive
        onClick={() => {
          onDelete();
          onClose();
        }}
      />
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  destructive,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      role="menuitem"
      onClick={onClick}
      className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-muted ${
        destructive ? 'text-destructive' : ''
      }`}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

// ── Status pill ─────────────────────────────────────────────────────────────

function StatusPill({ status }: { status: OAuthClientStatus }) {
  if (status === 'authorized') {
    return (
      <Badge variant="success">
        <ShieldCheck className="w-3 h-3 mr-1" />
        Authorized
      </Badge>
    );
  }
  if (status === 'expired') {
    return <Badge variant="warning">Expired</Badge>;
  }
  if (status === 'error') {
    return <Badge variant="error">Error</Badge>;
  }
  if (status === 'revoked') {
    return (
      <Badge variant="default">
        <span className="line-through">Revoked</span>
      </Badge>
    );
  }
  return <Badge variant="default">Pending</Badge>;
}

// ── Scopes hovercard ────────────────────────────────────────────────────────

function ScopesCell({ scopes }: { scopes: string[] }) {
  if (scopes.length === 0) return <span className="text-xs text-muted-foreground">none</span>;
  return (
    <details className="group">
      <summary className="cursor-pointer list-none text-xs">
        <span className="underline decoration-dotted">{scopes.length}</span>
      </summary>
      <ul className="mt-1 space-y-0.5 text-[11px] font-mono text-muted-foreground">
        {scopes.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </details>
  );
}

// ── Table primitives ────────────────────────────────────────────────────────

function Th({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      className={`text-left font-medium px-3 py-2 ${className ?? ''}`}
    >
      {children}
    </th>
  );
}

function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-3 py-2 align-middle ${className ?? ''}`}>{children}</td>;
}

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatRelative(iso: string): string {
  try {
    const then = new Date(iso).getTime();
    const now = Date.now();
    const diff = Math.max(0, now - then);
    const s = Math.floor(diff / 1000);
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h ago`;
    const d = Math.floor(h / 24);
    return `${d}d ago`;
  } catch {
    return iso;
  }
}
