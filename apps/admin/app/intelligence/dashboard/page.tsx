'use client';

import { useEffect, useState } from 'react';
import { Badge, Card, Skeleton, EmptyState } from '@/components/ui/legacy';
import type { BadgeVariant } from '@/components/ui/legacy';
import {
  Cpu,
  Languages,
  Mic,
  Eye,
  GitBranch,
  FileText,
  Sparkles,
  Network,
  CheckCircle,
  XCircle,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { adminApi } from '@/utils/api';
import type { IntelligenceComponent } from '@/utils/types';

// ---------------------------------------------------------------------------
// Component presentation metadata (label + icon per known component name).
// Any component the backend returns that isn't listed here still renders with
// a sensible default label and icon.
// ---------------------------------------------------------------------------

const COMPONENT_META: Record<string, { label: string; icon: LucideIcon }> = {
  embedder: { label: 'Embedder', icon: Cpu },
  entity_extractor: { label: 'Entity Extraction', icon: Sparkles },
  relation_extractor: { label: 'Relation Extraction', icon: FileText },
  language: { label: 'Language Detection', icon: Languages },
  vision: { label: 'Vision', icon: Eye },
  transcriber: { label: 'Transcription', icon: Mic },
  graph: { label: 'Graph Builder', icon: GitBranch },
};

function metaFor(name: string): { label: string; icon: LucideIcon } {
  return (
    COMPONENT_META[name] ?? {
      label: name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()),
      icon: Network,
    }
  );
}

function availabilityBadge(available: boolean): { variant: BadgeVariant; icon: LucideIcon; label: string } {
  return available
    ? { variant: 'success', icon: CheckCircle, label: 'Available' }
    : { variant: 'default', icon: XCircle, label: 'Unavailable' };
}

function renderConfig(config: Record<string, unknown>): string {
  const entries = Object.entries(config).filter(([, v]) => v !== null && v !== undefined && v !== '');
  if (entries.length === 0) return '';
  return entries.map(([k, v]) => `${k}: ${String(v)}`).join(' · ');
}

export default function IntelligenceDashboardPage() {
  const [components, setComponents] = useState<IntelligenceComponent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await adminApi.getIntelligenceStatus();
        if (cancelled) return;
        setComponents(status.components);
        setError(status.error ?? null);
      } catch {
        if (!cancelled) setError('Failed to load intelligence status.');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-lg">
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Intelligence Dashboard
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Live status of the Intelligence Layer: embeddings, entity and relation extraction, language
          detection, multimodal processing, and the graph pipeline.
        </p>
      </div>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-4 py-3 text-error text-sm mb-md" role="alert">
          {error}
        </div>
      )}

      {/* Component cards */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-lg">
          <Card className="!p-4"><Skeleton lines={2} /></Card>
          <Card className="!p-4"><Skeleton lines={2} /></Card>
          <Card className="!p-4"><Skeleton lines={2} /></Card>
        </div>
      ) : components.length === 0 ? (
        <Card>
          <EmptyState
            title="No intelligence components reported"
            description="The intelligence registry returned no components. Install optional dependencies to enable the Intelligence Layer."
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-lg">
          {components.map((c) => {
            const meta = metaFor(c.name);
            const Icon = meta.icon;
            const badge = availabilityBadge(c.available);
            const BadgeIcon = badge.icon;
            const configSummary = renderConfig(c.config);
            return (
              <Card key={c.name} className="!p-4">
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <Icon size={16} className="text-text-muted" />
                    <span className="text-xs text-text-muted uppercase tracking-wide">{meta.label}</span>
                  </div>
                  <Badge variant={badge.variant}>
                    <span className="flex items-center gap-1">
                      <BadgeIcon size={10} />
                      {badge.label}
                    </span>
                  </Badge>
                </div>
                <div className="text-lg font-bold text-text-primary break-words">{c.impl}</div>
                {configSummary && (
                  <div className="text-xs text-text-muted mt-1 break-words">{configSummary}</div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      {/* Component detail grid */}
      {!loading && components.length > 0 && (
        <Card>
          <h2 className="text-lg font-bold font-[family-name:var(--font-heading)] text-text-primary mt-0 mb-md">
            Component Detail
          </h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-collapse">
              <thead>
                <tr className="border-b-2 border-border">
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Component
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Implementation
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Config
                  </th>
                  <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {components.map((c) => {
                  const meta = metaFor(c.name);
                  const Icon = meta.icon;
                  const badge = availabilityBadge(c.available);
                  const BadgeIcon = badge.icon;
                  const configSummary = renderConfig(c.config);
                  return (
                    <tr
                      key={c.name}
                      className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors"
                    >
                      <td className="py-2.5 px-3 font-medium">
                        <span className="flex items-center gap-2">
                          <Icon size={14} className="text-text-muted" />
                          {meta.label}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-text-secondary font-[family-name:var(--font-mono)] text-xs">
                        {c.impl}
                      </td>
                      <td className="py-2.5 px-3 text-text-muted text-xs">{configSummary || '—'}</td>
                      <td className="py-2.5 px-3">
                        <Badge variant={badge.variant}>
                          <span className="flex items-center gap-1">
                            <BadgeIcon size={10} />
                            {badge.label}
                          </span>
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-md pt-md border-t border-border text-xs text-text-muted">
            Install optional dependencies to activate unavailable components:{' '}
            <code className="font-[family-name:var(--font-mono)] text-xs bg-bg-subtle px-1.5 py-0.5 rounded">
              pip install sagewai[intelligence]
            </code>{' '}
            or for all features including Whisper:{' '}
            <code className="font-[family-name:var(--font-mono)] text-xs bg-bg-subtle px-1.5 py-0.5 rounded">
              pip install sagewai[intelligence-full]
            </code>
          </div>
        </Card>
      )}
    </div>
  );
}
