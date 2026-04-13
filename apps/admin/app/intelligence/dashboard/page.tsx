'use client';

import { Badge, Card } from '@/components/ui/legacy';
import type { BadgeVariant } from '@/components/ui/legacy';
import {
  Brain,
  Cpu,
  Languages,
  Mic,
  Eye,
  GitBranch,
  Layers,
  FileText,
  MessageSquare,
  Sparkles,
  CheckCircle,
  AlertTriangle,
  XCircle,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

// ---------------------------------------------------------------------------
// Demo data — mirrors SDK intelligence module state
// ---------------------------------------------------------------------------

const DEMO_INTELLIGENCE = {
  embedder: {
    type: 'SentenceTransformerEmbedder',
    model: 'all-MiniLM-L6-v2',
    dimension: 384,
    status: 'active' as const,
  },
  extraction: {
    type: 'GLiNEREntityExtractor',
    model: 'urchade/gliner_medium-v2.1',
    status: 'active' as const,
  },
  fact_extraction: {
    type: 'HybridFactExtractor',
    mode: 'rules+llm',
    status: 'active' as const,
  },
  language: {
    detector: 'lingua',
    languages: ['en', 'de', 'tr', 'ru', 'zh', 'ja', 'ko', 'ar', 'fr', 'es'],
    status: 'active' as const,
  },
  multimodal: {
    transcription: 'FasterWhisperTranscriber',
    vision: 'StubVisionDescriber',
    status: 'partial' as const,
  },
  summarizer: {
    type: 'SemanticSummarizer',
    mode: 'embedding-scored',
    status: 'active' as const,
  },
  graph: {
    builder: 'ConversationGraphBuilder',
    entities: 142,
    relations: 87,
    status: 'active' as const,
  },
  consolidation: {
    last_run: '2026-03-31T10:00:00Z',
    facts_deduped: 12,
    contradictions: 2,
    status: 'active' as const,
  },
};

type IntelStatus = 'active' | 'partial' | 'inactive';

const STATUS_BADGE: Record<IntelStatus, BadgeVariant> = {
  active: 'success',
  partial: 'warning',
  inactive: 'default',
};

const STATUS_ICON: Record<IntelStatus, LucideIcon> = {
  active: CheckCircle,
  partial: AlertTriangle,
  inactive: XCircle,
};

// ---------------------------------------------------------------------------
// Feature grid rows
// ---------------------------------------------------------------------------

interface FeatureRow {
  phase: string;
  feature: string;
  backend: string;
  status: IntelStatus;
  icon: LucideIcon;
}

const FEATURES: FeatureRow[] = [
  { phase: 'I1', feature: 'Embeddings', backend: 'SentenceTransformer', status: 'active', icon: Cpu },
  { phase: 'I2', feature: 'Multi-Language', backend: 'lingua (10 languages)', status: 'active', icon: Languages },
  { phase: 'I3', feature: 'Entity Extraction', backend: 'GLiNER', status: 'active', icon: Sparkles },
  { phase: 'I4', feature: 'Fact Extraction', backend: 'Hybrid (rules+LLM)', status: 'active', icon: FileText },
  { phase: 'I5', feature: 'Multimodal Messages', backend: 'ContentPart', status: 'active', icon: MessageSquare },
  { phase: 'I6', feature: 'Transcription', backend: 'FasterWhisper', status: 'active', icon: Mic },
  { phase: 'I6', feature: 'Vision', backend: 'Stub', status: 'inactive', icon: Eye },
  { phase: 'I7', feature: 'Summarization', backend: 'Semantic', status: 'active', icon: Layers },
  { phase: 'I8', feature: 'Graph Builder', backend: 'Conversation to Graph', status: 'active', icon: GitBranch },
  { phase: 'I9', feature: 'Consolidation', backend: 'Dedup+Decay', status: 'active', icon: Brain },
];

// ---------------------------------------------------------------------------
// Stat card component
// ---------------------------------------------------------------------------

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  status,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  sub?: string;
  status: IntelStatus;
}) {
  const StatusIcon = STATUS_ICON[status];
  return (
    <Card className="!p-4">
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <Icon size={16} className="text-text-muted" />
          <span className="text-xs text-text-muted uppercase tracking-wide">{label}</span>
        </div>
        <Badge variant={STATUS_BADGE[status]}>
          <span className="flex items-center gap-1">
            <StatusIcon size={10} />
            {status}
          </span>
        </Badge>
      </div>
      <div className="text-lg font-bold text-text-primary">{value}</div>
      {sub && <div className="text-xs text-text-muted mt-0.5">{sub}</div>}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function IntelligenceDashboardPage() {
  const d = DEMO_INTELLIGENCE;

  return (
    <div className="max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-lg">
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Intelligence Dashboard
        </h1>
        <p className="mt-0 text-sm text-text-secondary">
          Overview of the Intelligence Layer: embeddings, extraction, language support, multimodal
          processing, and memory consolidation.
        </p>
      </div>

      {/* Summary stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-lg">
        <StatCard
          icon={Cpu}
          label="Embedder"
          value={d.embedder.model}
          sub={`${d.embedder.dimension}d / ${d.embedder.type}`}
          status={d.embedder.status}
        />
        <StatCard
          icon={Sparkles}
          label="Entity Extraction"
          value={d.extraction.type.replace('EntityExtractor', '')}
          sub={d.extraction.model}
          status={d.extraction.status}
        />
        <StatCard
          icon={Languages}
          label="Languages"
          value={`${d.language.languages.length} languages`}
          sub={d.language.languages.join(', ')}
          status={d.language.status}
        />
        <StatCard
          icon={Mic}
          label="Multimodal"
          value={d.multimodal.transcription.replace('Transcriber', '')}
          sub={`Vision: ${d.multimodal.vision.replace('Describer', '')}`}
          status={d.multimodal.status}
        />
      </div>

      {/* Provider configuration */}
      <Card className="mb-lg">
        <h2 className="text-lg font-bold font-[family-name:var(--font-heading)] text-text-primary mt-0 mb-md">
          Provider Configuration
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
          <div className="space-y-3">
            <div>
              <span className="text-text-muted">Embedder</span>
              <div className="font-medium text-text-primary">{d.embedder.type}</div>
              <div className="text-xs text-text-muted">
                Model: {d.embedder.model} ({d.embedder.dimension}d)
              </div>
            </div>
            <div>
              <span className="text-text-muted">Entity Extraction</span>
              <div className="font-medium text-text-primary">{d.extraction.type}</div>
              <div className="text-xs text-text-muted">Model: {d.extraction.model}</div>
            </div>
            <div>
              <span className="text-text-muted">Fact Extraction</span>
              <div className="font-medium text-text-primary">{d.fact_extraction.type}</div>
              <div className="text-xs text-text-muted">Mode: {d.fact_extraction.mode}</div>
            </div>
            <div>
              <span className="text-text-muted">Language Detection</span>
              <div className="font-medium text-text-primary">{d.language.detector}</div>
              <div className="text-xs text-text-muted">
                {d.language.languages.length} languages supported
              </div>
            </div>
          </div>
          <div className="space-y-3">
            <div>
              <span className="text-text-muted">Summarizer</span>
              <div className="font-medium text-text-primary">{d.summarizer.type}</div>
              <div className="text-xs text-text-muted">Mode: {d.summarizer.mode}</div>
            </div>
            <div>
              <span className="text-text-muted">Transcription</span>
              <div className="font-medium text-text-primary">{d.multimodal.transcription}</div>
            </div>
            <div>
              <span className="text-text-muted">Vision</span>
              <div className="font-medium text-text-primary">{d.multimodal.vision}</div>
            </div>
            <div>
              <span className="text-text-muted">Graph Builder</span>
              <div className="font-medium text-text-primary">{d.graph.builder}</div>
              <div className="text-xs text-text-muted">
                {d.graph.entities} entities, {d.graph.relations} relations
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* Consolidation summary */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-lg">
        <Card className="!p-4">
          <div className="flex items-center gap-2 mb-1">
            <GitBranch size={16} className="text-text-muted" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Graph Store</span>
          </div>
          <div className="text-2xl font-bold">{d.graph.entities}</div>
          <div className="text-xs text-text-muted">
            entities / {d.graph.relations} relations
          </div>
        </Card>
        <Card className="!p-4">
          <div className="flex items-center gap-2 mb-1">
            <Layers size={16} className="text-text-muted" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Facts Deduped</span>
          </div>
          <div className="text-2xl font-bold">{d.consolidation.facts_deduped}</div>
          <div className="text-xs text-text-muted">last consolidation run</div>
        </Card>
        <Card className="!p-4">
          <div className="flex items-center gap-2 mb-1">
            <AlertTriangle size={16} className="text-amber-500" />
            <span className="text-xs text-text-muted uppercase tracking-wide">Contradictions</span>
          </div>
          <div className="text-2xl font-bold">{d.consolidation.contradictions}</div>
          <div className="text-xs text-text-muted">
            last run:{' '}
            {new Date(d.consolidation.last_run).toLocaleDateString('en-US', {
              month: 'short',
              day: 'numeric',
              hour: '2-digit',
              minute: '2-digit',
            })}
          </div>
        </Card>
      </div>

      {/* Feature availability grid */}
      <Card>
        <h2 className="text-lg font-bold font-[family-name:var(--font-heading)] text-text-primary mt-0 mb-md">
          Feature Availability
        </h2>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border">
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                  Phase
                </th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                  Feature
                </th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                  Backend
                </th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                  Status
                </th>
              </tr>
            </thead>
            <tbody>
              {FEATURES.map((f) => {
                const StatusIcon = STATUS_ICON[f.status];
                return (
                  <tr
                    key={`${f.phase}-${f.feature}`}
                    className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors"
                  >
                    <td className="py-2.5 px-3">
                      <span className="inline-block px-2 py-0.5 text-[11px] font-mono font-semibold bg-bg-subtle border border-border rounded">
                        {f.phase}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 font-medium">
                      <span className="flex items-center gap-2">
                        <f.icon size={14} className="text-text-muted" />
                        {f.feature}
                      </span>
                    </td>
                    <td className="py-2.5 px-3 text-text-secondary">{f.backend}</td>
                    <td className="py-2.5 px-3">
                      <Badge variant={STATUS_BADGE[f.status]}>
                        <span className="flex items-center gap-1">
                          <StatusIcon size={10} />
                          {f.status === 'active'
                            ? 'Active'
                            : f.status === 'partial'
                              ? 'Partial'
                              : 'Inactive'}
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
          Install optional dependencies to activate inactive features:{' '}
          <code className="font-[family-name:var(--font-mono)] text-xs bg-bg-subtle px-1.5 py-0.5 rounded">
            pip install sagewai[intelligence]
          </code>{' '}
          or for all features including BART and Whisper:{' '}
          <code className="font-[family-name:var(--font-mono)] text-xs bg-bg-subtle px-1.5 py-0.5 rounded">
            pip install sagewai[intelligence-full]
          </code>
        </div>
      </Card>
    </div>
  );
}
