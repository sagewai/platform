'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { adminApi } from '@/utils/api';
import type { AgentTemplate } from '@/utils/types';
import { AgentConfigPanel, type AgentConfigDefaults } from '@/components/agent-config-panel';
import { ArrowLeft } from 'lucide-react';
import Link from 'next/link';

export default function NewAgentPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const templateId = searchParams.get('templateId');

  const [defaults, setDefaults] = useState<AgentConfigDefaults | undefined>(undefined);
  const [loading, setLoading] = useState(!!templateId);
  const [templateName, setTemplateName] = useState('');

  useEffect(() => {
    if (!templateId) {
      setLoading(false);
      return;
    }
    adminApi
      .getAgentTemplate(templateId)
      .then((t: AgentTemplate) => {
        setTemplateName(t.name);
        setDefaults({
          name: t.name.toLowerCase().replace(/\s+/g, '-'),
          model: t.model,
          system_prompt: t.system_prompt,
          temperature: t.temperature,
          strategy: t.strategy,
          tools: t.tools,
          mcp_servers: t.mcp_servers,
          memory_backends: t.memory_backends,
          guardrails: t.guardrails,
        });
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [templateId]);

  const handleAgentCreated = (name: string) => {
    router.push(`/playground?agent=${encodeURIComponent(name)}`);
  };

  if (loading) {
    return (
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-center min-h-[400px]">
          <div className="w-6 h-6 border-2 border-primary border-t-transparent rounded-full animate-spin" />
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center gap-3 mb-md">
        <Link
          href="/agents/templates"
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors no-underline"
        >
          <ArrowLeft className="h-4 w-4" /> Templates
        </Link>
        {templateName && (
          <span className="text-sm text-muted-foreground">
            / Creating from <strong className="text-foreground">{templateName}</strong>
          </span>
        )}
      </div>

      <AgentConfigPanel
        onAgentCreated={handleAgentCreated}
        defaults={defaults}
      />
    </div>
  );
}
