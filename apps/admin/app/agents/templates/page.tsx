'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { PageLayout, Card, Badge, Button, Skeleton } from '@sagecurator/ui';
import { adminApi } from '@/utils/api';
import type { AgentTemplate } from '@/utils/types';

const CATEGORY_LABELS: Record<string, string> = {
  support: 'Support',
  engineering: 'Engineering',
  data: 'Data',
  research: 'Research',
  content: 'Content',
  operations: 'Operations',
  travel: 'Travel',
};

const STRATEGY_LABELS: Record<string, string> = {
  react: 'ReAct',
  lats: 'LATS',
  tree_of_thoughts: 'Tree of Thoughts',
  self_correction: 'Self-Correction',
};

export default function AgentTemplatesPage() {
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    adminApi.listAgentTemplates()
      .then(setTemplates)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <PageLayout title="Agent Templates" description="Pre-built agent configurations to get started quickly.">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-md">
          {[1, 2, 3].map((i) => (
            <Card key={i}><Skeleton lines={4} /></Card>
          ))}
        </div>
      </PageLayout>
    );
  }

  return (
    <PageLayout title="Agent Templates" description="Pre-built agent configurations with tools, MCP servers, memory, and guardrails.">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-md">
        {templates.map((t) => (
          <Card key={t.id} className="flex flex-col">
            <div className="flex items-center justify-between mb-sm">
              <h3 className="text-base font-semibold font-[family-name:var(--font-heading)] m-0">{t.name}</h3>
              <Badge variant="info">{CATEGORY_LABELS[t.category] || t.category}</Badge>
            </div>
            <p className="text-sm text-text-secondary mb-md flex-1">{t.description}</p>

            {/* Model + Strategy + Temperature */}
            <div className="flex flex-wrap gap-sm mb-2">
              <Badge variant="default">{t.model}</Badge>
              {t.strategy && (
                <Badge variant="default">{STRATEGY_LABELS[t.strategy] || t.strategy}</Badge>
              )}
              <Badge variant="default">temp: {t.temperature}</Badge>
            </div>

            {/* Capabilities summary */}
            <div className="flex flex-wrap gap-1 mb-md">
              {t.tools?.length > 0 && (
                <span className="text-[11px] bg-primary/10 text-primary px-1.5 py-0.5 rounded font-medium">
                  {t.tools.length} tool{t.tools.length > 1 ? 's' : ''}
                </span>
              )}
              {t.mcp_servers?.length > 0 && (
                <span className="text-[11px] bg-info/10 text-info px-1.5 py-0.5 rounded font-medium">
                  {t.mcp_servers.length} MCP
                </span>
              )}
              {t.memory_backends?.length > 0 && (
                <span className="text-[11px] bg-secondary/10 text-secondary px-1.5 py-0.5 rounded font-medium">
                  {t.memory_backends.length} memory
                </span>
              )}
              {t.guardrails?.length > 0 && (
                <span className="text-[11px] bg-warning/10 text-warning px-1.5 py-0.5 rounded font-medium">
                  {t.guardrails.length} guardrail{t.guardrails.length > 1 ? 's' : ''}
                </span>
              )}
            </div>

            <div className="bg-bg-subtle rounded-md p-sm mb-md text-xs text-text-secondary max-h-20 overflow-hidden">
              <span className="font-semibold">Prompt: </span>{t.system_prompt}
            </div>
            <Button
              size="sm"
              onClick={() => router.push(`/playground?template=${t.id}`)}
            >
              Use Template
            </Button>
          </Card>
        ))}
      </div>
    </PageLayout>
  );
}
