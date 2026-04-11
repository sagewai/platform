'use client';

import { useRouter } from 'next/navigation';
import { motion } from 'framer-motion';
import {
  Wrench,
  Plug,
  Brain,
  ShieldCheck,
  Play,
  ArrowRight,
} from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { getCategoryMeta, TONE_CLASSES } from './category-icon';
import type { AgentTemplate } from '@/utils/types';

const STRATEGY_LABELS: Record<string, string> = {
  react: 'ReAct',
  lats: 'LATS',
  tree_of_thoughts: 'Tree of Thoughts',
  self_correction: 'Self-Correction',
};

interface AgentTemplateCardProps {
  template: AgentTemplate;
}

export function AgentTemplateCard({ template }: AgentTemplateCardProps) {
  const router = useRouter();
  const meta = getCategoryMeta(template.category);
  const tone = TONE_CLASSES[meta.tone];
  const Icon = meta.Icon;

  const useTemplate = () => router.push(`/agents/new?templateId=${template.id}`);
  const quickRun = () => router.push(`/playground?templateId=${template.id}`);

  const capabilityChips: { Icon: typeof Wrench; count: number; label: string }[] = [
    { Icon: Wrench, count: template.tools?.length ?? 0, label: 'tools' },
    { Icon: Plug, count: template.mcp_servers?.length ?? 0, label: 'MCP' },
    { Icon: Brain, count: template.memory_backends?.length ?? 0, label: 'memory' },
    { Icon: ShieldCheck, count: template.guardrails?.length ?? 0, label: 'guardrails' },
  ].filter((c) => c.count > 0);

  return (
    <motion.div
      whileHover={{ y: -2 }}
      transition={{ duration: 0.15, ease: 'easeOut' }}
      className="group h-full"
    >
      <Card className="h-full flex flex-col transition-shadow group-hover:shadow-lg group-hover:border-primary/30">
        <CardContent className="flex flex-col flex-1 gap-3 p-5">
          {/* Header — tinted icon tile + name + category */}
          <div className="flex items-start gap-3">
            <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${tone.tile}`}>
              <Icon className="h-5 w-5" aria-hidden="true" />
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-base font-semibold m-0 truncate">{template.name}</h3>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className={`text-xs ${tone.text}`}>{meta.label}</span>
                <span className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> Available
                </span>
              </div>
            </div>
          </div>

          {/* Description */}
          <p className="text-sm text-muted-foreground line-clamp-2 m-0">{template.description}</p>

          {/* Capability chips */}
          {capabilityChips.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {capabilityChips.map(({ Icon: ChipIcon, count, label }) => (
                <Badge key={label} variant="secondary" className="gap-1 font-normal">
                  <ChipIcon className="h-3 w-3" />
                  {count} {label}
                </Badge>
              ))}
            </div>
          )}

          {/* Model / strategy / temperature */}
          <dl className="grid grid-cols-3 gap-2 text-[11px] mt-1">
            <div>
              <dt className="text-muted-foreground uppercase tracking-wider">Model</dt>
              <dd className="font-mono mt-0.5 truncate">{template.model}</dd>
            </div>
            {template.strategy && (
              <div>
                <dt className="text-muted-foreground uppercase tracking-wider">Strategy</dt>
                <dd className="mt-0.5 truncate">{STRATEGY_LABELS[template.strategy] || template.strategy}</dd>
              </div>
            )}
            <div>
              <dt className="text-muted-foreground uppercase tracking-wider">Temp</dt>
              <dd className="font-mono mt-0.5">{template.temperature}</dd>
            </div>
          </dl>

          {/* Optional system prompt preview */}
          <details className="mt-1 text-xs text-muted-foreground">
            <summary className="cursor-pointer hover:text-foreground transition-colors select-none">
              Prompt preview
            </summary>
            <p className="mt-2 m-0 line-clamp-4 italic">{template.system_prompt}</p>
          </details>

          {/* Spacer pushes actions to bottom */}
          <div className="flex-1" />

          {/* Actions */}
          <div className="flex items-center gap-2 pt-1">
            <Button onClick={useTemplate} className="flex-1">
              Use Template <ArrowRight className="ml-1.5 h-4 w-4" />
            </Button>
            <Button
              onClick={quickRun}
              variant="ghost"
              size="icon"
              aria-label="Quick run in playground"
              title="Quick run"
              className="opacity-0 group-hover:opacity-100 transition-opacity"
            >
              <Play className="h-4 w-4" />
            </Button>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  );
}
