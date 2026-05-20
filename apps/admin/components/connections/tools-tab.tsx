'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Button,
  Dialog,
  FormField,
  TextInput,
  useToast,
  Skeleton,
  EmptyState,
} from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type {
  ToolCredentialField,
  ToolRegistryEntry,
  ToolConnectionMetadata,
} from '@/utils/types';
import {
  CheckCircle2, AlertCircle, Trash2, RefreshCw, Plus,
} from 'lucide-react';

// ── ToolsTab ──────────────────────────────────────────────────────────────────

export function ToolsTab() {
  const [registry, setRegistry] = useState<ToolRegistryEntry[]>([]);
  const [connections, setConnections] = useState<ToolConnectionMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const { toast } = useToast();

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [reg, conn] = await Promise.all([
        adminApi.listToolRegistry(),
        adminApi.listToolConnections(),
      ]);
      setRegistry(reg);
      setConnections(conn);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    refresh().finally(() => setLoading(false));
  }, [refresh]);

  async function handleTest(toolId: string, title: string) {
    setTestingId(toolId);
    try {
      const result = await adminApi.testToolConnection(toolId);
      toast(
        result.ok ? 'success' : 'error',
        `${title}: ${result.ok ? 'Connection verified' : (result.error ?? 'Test failed')}`,
      );
      await refresh();
    } catch (e) {
      toast('error', `Test error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTestingId(null);
    }
  }

  async function handleDelete(toolId: string, title: string) {
    setDeletingId(toolId);
    try {
      await adminApi.deleteToolConnection(toolId);
      toast('success', `${title} credentials removed`);
      await refresh();
    } catch (e) {
      toast('error', `Delete failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDeletingId(null);
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
        title="Could not load tool connections"
        description={error}
        actionLabel="Retry"
        onAction={() => { setLoading(true); refresh().finally(() => setLoading(false)); }}
      />
    );
  }

  const connectedIds = new Set(connections.map((c) => c.tool_id));

  return (
    <div className="mt-4 space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {registry.length} tool{registry.length !== 1 ? 's' : ''} in catalogue
          {connections.length > 0 && ` · ${connections.length} connected`}
        </p>
        <Button
          size="sm"
          onClick={() => setShowAddModal(true)}
          disabled={registry.length === 0 || connectedIds.size >= registry.length}
        >
          <Plus className="w-3.5 h-3.5 mr-1.5" />
          Add tool connection
        </Button>
      </div>

      {connections.length === 0 ? (
        <div
          data-testid="tool-connections-empty"
          className="rounded-md border border-dashed p-12 text-center text-sm text-muted-foreground"
        >
          <h3 className="text-base font-medium text-foreground">No tool connections yet</h3>
          <p className="mt-2">
            Click &ldquo;Add tool connection&rdquo; to register a credential for any of
            the {registry.length} catalogued tool{registry.length !== 1 ? 's' : ''}.
          </p>
        </div>
      ) : (
        <div
          data-testid="tool-connections-list"
          className="rounded-lg border border-border divide-y"
        >
          {connections.map((c) => {
            const entry = registry.find((r) => r.id === c.tool_id);
            return (
              <ToolConnectionRow
                key={c.tool_id}
                connection={c}
                entry={entry}
                isTesting={testingId === c.tool_id}
                isDeleting={deletingId === c.tool_id}
                onTest={() => handleTest(c.tool_id, c.catalogue_title)}
                onDelete={() => handleDelete(c.tool_id, c.catalogue_title)}
              />
            );
          })}
        </div>
      )}

      {showAddModal && (
        <AddToolModal
          registry={registry}
          connectedIds={connectedIds}
          onClose={() => setShowAddModal(false)}
          onAdded={async () => {
            setShowAddModal(false);
            await refresh();
          }}
        />
      )}
    </div>
  );
}

// ── ToolConnectionRow ─────────────────────────────────────────────────────────

function ToolConnectionRow({
  connection,
  entry,
  isTesting,
  isDeleting,
  onTest,
  onDelete,
}: {
  connection: ToolConnectionMetadata;
  entry: ToolRegistryEntry | undefined;
  isTesting: boolean;
  isDeleting: boolean;
  onTest: () => void;
  onDelete: () => void;
}) {
  const statusOk = connection.status === 'ok';
  const statusFailed = connection.status === 'failed';

  return (
    <div className="flex items-center justify-between p-4">
      <div>
        <div className="flex items-center gap-2">
          <span className="font-medium">{connection.catalogue_title}</span>
          {entry?.category && (
            <span className="text-xs rounded-full bg-muted px-2 py-0.5 text-muted-foreground">
              {entry.category}
            </span>
          )}
          {statusOk && (
            <span className="inline-flex items-center gap-1 text-xs text-green-600">
              <CheckCircle2 className="w-3 h-3" />
              Verified
            </span>
          )}
          {statusFailed && (
            <span className="inline-flex items-center gap-1 text-xs text-destructive">
              <AlertCircle className="w-3 h-3" />
              Test failed
            </span>
          )}
        </div>
        <div className="text-xs text-muted-foreground mt-0.5">
          {connection.last_tested_at
            ? `Last tested ${formatTime(connection.last_tested_at)}`
            : 'Never tested'}
          {connection.updated_at && (
            <> · Updated {formatTime(connection.updated_at)}</>
          )}
        </div>
      </div>

      <div className="flex gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={onTest}
          disabled={isTesting || isDeleting}
        >
          <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isTesting ? 'animate-spin' : ''}`} />
          {isTesting ? 'Testing…' : 'Test'}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onDelete}
          disabled={isTesting || isDeleting}
          className="text-destructive"
        >
          <Trash2 className="w-3.5 h-3.5 mr-1.5" />
          {isDeleting ? 'Removing…' : 'Remove'}
        </Button>
      </div>
    </div>
  );
}

// ── AddToolModal ──────────────────────────────────────────────────────────────

function AddToolModal({
  registry,
  connectedIds,
  onClose,
  onAdded,
}: {
  registry: ToolRegistryEntry[];
  connectedIds: Set<string>;
  onClose: () => void;
  onAdded: () => Promise<void>;
}) {
  const available = registry.filter((r) => !connectedIds.has(r.id));
  const [selectedId, setSelectedId] = useState<string>(available[0]?.id ?? '');
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  const selected = available.find((r) => r.id === selectedId) ?? null;

  function handleSelectChange(id: string) {
    setSelectedId(id);
    setCredentials({});
    setError(null);
  }

  async function handleSubmit() {
    if (!selectedId || !selected) return;
    setSubmitting(true);
    setError(null);
    try {
      await adminApi.upsertToolConnection(selectedId, credentials);
      // Best-effort test — failure doesn't block the add.
      try {
        const result = await adminApi.testToolConnection(selectedId);
        if (!result.ok) {
          toast('info', `${selected.title} saved but test failed: ${result.error ?? 'unknown error'}`);
        } else {
          toast('success', `${selected.title} credentials saved and verified.`);
        }
      } catch {
        toast('success', `${selected.title} credentials saved (test skipped).`);
      }
      await onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title="Add tool connection"
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!selectedId || submitting}>
            {submitting ? 'Adding…' : 'Save credentials'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <FormField label="Tool" hint="Choose the tool to configure.">
          <select
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            value={selectedId}
            onChange={(e) => handleSelectChange(e.target.value)}
          >
            {available.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title} ({r.category})
              </option>
            ))}
          </select>
        </FormField>

        {selected && (
          <>
            <p className="text-xs text-muted-foreground">
              {selected.description}
              {selected.signup_url && (
                <>
                  {' '}
                  <a
                    href={selected.signup_url}
                    target="_blank"
                    rel="noreferrer"
                    className="underline"
                  >
                    Create account
                  </a>
                  {selected.console_path && (
                    <>
                      {' · '}
                      <a
                        href={`${selected.signup_url}${selected.console_path}`}
                        target="_blank"
                        rel="noreferrer"
                        className="underline"
                      >
                        API console
                      </a>
                    </>
                  )}
                </>
              )}
            </p>

            {selected.credential_fields.map((field: ToolCredentialField) => (
              <FormField
                key={field.name}
                label={field.label}
                hint={field.description}
              >
                <TextInput
                  type={field.type}
                  value={credentials[field.name] ?? ''}
                  onChange={(e) =>
                    setCredentials({ ...credentials, [field.name]: e.target.value })
                  }
                  placeholder={field.label}
                  autoComplete="off"
                />
              </FormField>
            ))}
          </>
        )}

        {error && (
          <div className="text-destructive text-xs" role="alert">
            {error}
          </div>
        )}

        <p className="text-xs text-muted-foreground border-t pt-3">
          Credentials are stored encrypted-at-rest via Sealed (Fernet, AES-128-CBC + HMAC).
        </p>
      </div>
    </Dialog>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
