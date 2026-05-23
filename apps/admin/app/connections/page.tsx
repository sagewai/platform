'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Skeleton,
  Tabs,
  useToast,
} from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type {
  InferenceProviderCatalogEntry,
  InferenceProviderKey,
  InferenceProviderMetadata,
} from '@/utils/types';
import {
  InferenceProviderModal,
  deleteProviderCredentials,
} from '@/components/inference-provider-modal';
import { ToolsTab } from '@/components/connections/tools-tab';
import { OAuthTab } from '@/components/connections/oauth-tab';
import {
  CheckCircle2, AlertCircle, ExternalLink, KeyRound, RefreshCw, Trash2,
} from 'lucide-react';

type ConnectionTab = 'inference' | 'tools' | 'oauth';

const TABS = [
  { id: 'inference', label: 'Inference' },
  { id: 'tools', label: 'Tools' },
  { id: 'oauth', label: 'OAuth' },
] as const;

export default function ConnectionsPage() {
  const { currentSlug } = useProject();
  const [catalog, setCatalog] = useState<InferenceProviderCatalogEntry[]>([]);
  const [providers, setProviders] = useState<InferenceProviderMetadata[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<InferenceProviderKey | null>(null);
  const [deleting, setDeleting] = useState<InferenceProviderKey | null>(null);
  const [testingKey, setTestingKey] = useState<InferenceProviderKey | null>(null);
  const [activeTab, setActiveTab] = useState<ConnectionTab>('inference');
  const { toast } = useToast();

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const list = await adminApi.listInferenceProviders();
      setProviders(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    Promise.all([
      adminApi.getInferenceProviderCatalog(),
      adminApi.listInferenceProviders(),
    ])
      .then(([cat, list]) => {
        setCatalog(cat.providers);
        setProviders(list);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false));
  }, [currentSlug]);

  const catalogByKey = useMemo(
    () => Object.fromEntries(catalog.map((c) => [c.provider, c])),
    [catalog],
  );

  async function onTest(provider: InferenceProviderKey) {
    setTestingKey(provider);
    try {
      const result = await adminApi.testInferenceProvider(provider);
      const label = catalogByKey[provider]?.label ?? provider;
      toast(
        result.ok ? 'success' : 'error',
        `${label}: ${result.detail}`,
      );
      await refresh();
    } catch (e) {
      toast('error', `Test connection error: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setTestingKey(null);
    }
  }

  async function onDelete(provider: InferenceProviderKey) {
    try {
      await deleteProviderCredentials(provider);
      const label = catalogByKey[provider]?.label ?? provider;
      toast('success', `${label} credentials cleared`);
      await refresh();
    } catch (e) {
      toast('error', `Delete failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setDeleting(null);
    }
  }

  const editingEntry = editing ? catalogByKey[editing] : null;
  const editingCurrent = editing
    ? providers.find((p) => p.provider === editing) ?? null
    : null;

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-lg">
        <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Connections
        </h1>
        <p className="mt-0 text-sm text-muted-foreground">
          Manage encrypted credentials for inference providers and tool connections.
          Stored encrypted-at-rest via Sealed; project-scoped to{' '}
          <code className="text-xs">{currentSlug ?? 'org-global'}</code>.
        </p>
      </div>

      <Tabs
        tabs={TABS as unknown as Array<{ id: string; label: string }>}
        active={activeTab}
        onChange={(id) => setActiveTab(id as ConnectionTab)}
      />

      {activeTab === 'inference' && (
        <>
          <ProviderSpectrumBanner />

          {loading && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <Skeleton lines={5} />
              <Skeleton lines={5} />
              <Skeleton lines={5} />
              <Skeleton lines={5} />
            </div>
          )}

          {error && !loading && (
            <EmptyState
              title="Could not load credential vault"
              description={error}
              actionLabel="Retry"
              onAction={refresh}
            />
          )}

          {!loading && !error && (
            <div
              data-testid="inference-provider-grid"
              className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4"
            >
              {providers.map((p) => {
                const cat = catalogByKey[p.provider];
                if (!cat) return null;
                return (
                  <ProviderCard
                    key={p.provider}
                    provider={p}
                    catalog={cat}
                    onEdit={() => setEditing(p.provider)}
                    onTest={() => onTest(p.provider)}
                    onDelete={() => setDeleting(p.provider)}
                    isTesting={testingKey === p.provider}
                  />
                );
              })}
            </div>
          )}
        </>
      )}

      {activeTab === 'tools' && (
        <ToolsTab />
      )}

      {activeTab === 'oauth' && <OAuthTab />}

      {editingEntry && (
        <InferenceProviderModal
          open
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh();
          }}
          catalogEntry={editingEntry}
          current={editingCurrent}
        />
      )}

      {deleting && (
        <ConfirmDialog
          open
          onClose={() => setDeleting(null)}
          onConfirm={() => onDelete(deleting)}
          title={`Clear ${catalogByKey[deleting]?.label ?? deleting} credentials?`}
          message="This permanently removes the encrypted credential blob for this project. Other projects retain their own credentials."
          confirmLabel="Clear credentials"
        />
      )}
    </div>
  );
}

function ProviderCard({
  provider, catalog, onEdit, onTest, onDelete, isTesting,
}: {
  provider: InferenceProviderMetadata;
  catalog: InferenceProviderCatalogEntry;
  onEdit: () => void;
  onTest: () => void;
  onDelete: () => void;
  isTesting: boolean;
}) {
  const status = !provider.configured
    ? 'not_set'
    : provider.last_test_ok === true
      ? 'verified'
      : provider.last_test_ok === false
        ? 'failed'
        : 'set';
  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <div className="flex items-start justify-between mb-3">
        <div>
          <div className="flex items-center gap-2">
            <ProviderLogo provider={provider.provider} />
            <h3 className="text-lg font-semibold">{catalog.label}</h3>
            <StatusBadge status={status} />
          </div>
          <p className="text-xs text-muted-foreground mt-1">{catalog.tagline}</p>
        </div>
      </div>

      <dl className="text-xs space-y-1.5 mb-4">
        <Row label="Required keys">
          <span className="font-mono">{catalog.secret_keys.join(', ')}</span>
        </Row>
        {provider.last_updated_at && (
          <Row label="Last updated">{formatTime(provider.last_updated_at)}</Row>
        )}
        {provider.last_tested_at && (
          <Row label="Last tested">
            {formatTime(provider.last_tested_at)}
            {provider.last_test_detail && (
              <span className="text-muted-foreground"> — {provider.last_test_detail}</span>
            )}
          </Row>
        )}
        {catalog.example && (
          <Row label="Companion example">
            <code className="font-mono">{catalog.example}.py</code>
          </Row>
        )}
      </dl>

      <div className="flex gap-2 flex-wrap">
        <Button onClick={onEdit} size="sm">
          <KeyRound className="w-3.5 h-3.5 mr-1.5" />
          {provider.configured ? 'Edit credentials' : 'Add credentials'}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={onTest}
          disabled={!provider.configured || isTesting}
        >
          <RefreshCw
            className={`w-3.5 h-3.5 mr-1.5 ${isTesting ? 'animate-spin' : ''}`}
          />
          {isTesting ? 'Testing…' : 'Test connection'}
        </Button>
        {provider.configured && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            className="text-destructive"
          >
            <Trash2 className="w-3.5 h-3.5 mr-1.5" />
            Clear
          </Button>
        )}
      </div>
    </div>
  );
}

function ProviderSpectrumBanner() {
  return (
    <div className="rounded-lg border border-dashed border-border p-4 bg-muted/30 text-xs">
      <div className="font-semibold mb-2">The v1.0 inference spectrum</div>
      <ul className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-1 text-muted-foreground">
        <li>
          <strong>Colab</strong> — free Tesla T4 (Tier 1 — example 44).
        </li>
        <li>
          <strong>Vast.ai</strong> — budget marketplace (Tier 2 — example 45).
        </li>
        <li>
          <strong>RunPod</strong> — reliable CLI default (Tier 3 — example 47).
        </li>
        <li>
          <strong>Modal</strong> — serverless inference (Tier 4 — example 48).
        </li>
        <li>
          <strong>Custom</strong> — bring-your-own endpoint (example 46).
        </li>
        <li>
          <a className="underline inline-flex items-center gap-1" href="/docs/inference">
            Education docs <ExternalLink className="w-3 h-3" />
          </a>
        </li>
      </ul>
      <p className="text-muted-foreground mt-2">
        v1.0 ships the credential vault only. Templates, trigger flow, and
        live job monitoring are scheduled for v1.1.
      </p>
    </div>
  );
}

function ProviderLogo({ provider }: { provider: InferenceProviderKey }) {
  const colour = LOGO_COLOURS[provider];
  return (
    <div
      aria-hidden
      className="w-8 h-8 rounded-md flex items-center justify-center text-xs font-bold uppercase"
      style={{ background: colour.bg, color: colour.fg }}
    >
      {provider === 'vastai' ? 'va' : provider.slice(0, 2)}
    </div>
  );
}

const LOGO_COLOURS: Record<InferenceProviderKey, { bg: string; fg: string }> = {
  runpod: { bg: '#e1b455', fg: '#1f1300' },
  modal: { bg: '#7c4dff', fg: '#ffffff' },
  vastai: { bg: '#1f7d4a', fg: '#ffffff' },
  colab: { bg: '#f9ab00', fg: '#1f1300' },
  custom: { bg: '#475569', fg: '#ffffff' },
};

function StatusBadge({ status }: { status: 'not_set' | 'set' | 'verified' | 'failed' }) {
  if (status === 'verified') {
    return (
      <Badge variant="success">
        <CheckCircle2 className="w-3 h-3 mr-1" />
        Verified
      </Badge>
    );
  }
  if (status === 'failed') {
    return (
      <Badge variant="error">
        <AlertCircle className="w-3 h-3 mr-1" />
        Test failed
      </Badge>
    );
  }
  if (status === 'set') {
    return <Badge variant="info">Set (untested)</Badge>;
  }
  return <Badge variant="default">Not configured</Badge>;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2">
      <dt className="text-muted-foreground w-32 shrink-0">{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
