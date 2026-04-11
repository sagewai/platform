'use client';

import { useEffect, useState } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { Card, CardContent } from '@/components/ui/card';
import { adminApi } from '@/utils/api';
import { AgentTemplateCard } from '@/components/agent-template-card';
import type { AgentTemplate } from '@/utils/types';

export default function AgentTemplatesPage() {
  const [templates, setTemplates] = useState<AgentTemplate[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    adminApi
      .listAgentTemplates()
      .then(setTemplates)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-lg">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mt-0 mb-1">
          Agent Templates
        </h1>
        <p className="text-sm text-muted-foreground m-0">
          Pre-built agent configurations with tools, MCP servers, memory, and guardrails.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-md">
        {loading
          ? Array.from({ length: 6 }).map((_, i) => (
              <Card key={i}>
                <CardContent className="p-5 space-y-3">
                  <div className="flex items-start gap-3">
                    <Skeleton className="h-10 w-10 rounded-lg" />
                    <div className="flex-1 space-y-1.5">
                      <Skeleton className="h-4 w-32" />
                      <Skeleton className="h-3 w-20" />
                    </div>
                  </div>
                  <Skeleton className="h-4 w-full" />
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-9 w-full mt-2" />
                </CardContent>
              </Card>
            ))
          : templates.map((t) => <AgentTemplateCard key={t.id} template={t} />)}
      </div>
    </div>
  );
}
