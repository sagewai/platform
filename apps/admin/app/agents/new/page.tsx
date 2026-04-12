'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { adminApi } from '@/utils/api';
import type { AgentTemplate } from '@/utils/types';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ArrowLeft, Bot, Wrench, Plug, Brain, ShieldCheck, Sparkles, Play } from 'lucide-react';
import Link from 'next/link';

export default function NewAgentPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const templateId = searchParams.get('templateId');

  const [template, setTemplate] = useState<AgentTemplate | null>(null);
  const [loading, setLoading] = useState(!!templateId);

  // Form state — pre-filled from template when available
  const [name, setName] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [model, setModel] = useState('gpt-4o-mini');
  const [temperature, setTemperature] = useState(0.7);
  const [strategy, setStrategy] = useState('single');

  useEffect(() => {
    if (!templateId) return;
    adminApi
      .getAgentTemplate(templateId)
      .then((t) => {
        setTemplate(t);
        setName(t.name);
        setSystemPrompt(t.system_prompt);
        setModel(t.model);
        setTemperature(t.temperature);
        setStrategy(t.strategy);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [templateId]);

  const handleCreate = () => {
    // Navigate to playground with the agent config pre-filled
    const params = new URLSearchParams({
      name,
      system_prompt: systemPrompt,
      model,
      temperature: String(temperature),
      strategy,
    });
    if (templateId) params.set('templateId', templateId);
    router.push(`/playground?${params.toString()}`);
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

  const capabilities = [
    { icon: Wrench, items: template?.tools ?? [], label: 'Tools' },
    { icon: Plug, items: template?.mcp_servers ?? [], label: 'MCP Servers' },
    { icon: Brain, items: template?.memory_backends ?? [], label: 'Memory' },
    { icon: ShieldCheck, items: template?.guardrails ?? [], label: 'Guardrails' },
  ].filter((c) => c.items.length > 0);

  return (
    <div className="max-w-3xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-3 mb-lg">
        <Link
          href="/agents/templates"
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors no-underline"
        >
          <ArrowLeft className="h-4 w-4" /> Templates
        </Link>
      </div>

      <div className="mb-lg">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mt-0 mb-1 flex items-center gap-2">
          <Bot className="h-6 w-6 text-primary" />
          {template ? `Create from: ${template.name}` : 'Create New Agent'}
        </h1>
        {template && (
          <p className="text-sm text-muted-foreground m-0 mt-1">
            {template.description}
          </p>
        )}
      </div>

      {/* Template capabilities summary */}
      {capabilities.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-lg">
          {capabilities.map(({ icon: Icon, items, label }) => (
            <Badge key={label} variant="secondary" className="gap-1.5 py-1">
              <Icon className="h-3.5 w-3.5" />
              {items.length} {label}: {items.join(', ')}
            </Badge>
          ))}
        </div>
      )}

      {/* Configuration form */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Agent Configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="agent-name">Agent Name</Label>
            <Input
              id="agent-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-agent"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="system-prompt">System Prompt</Label>
            <Textarea
              id="system-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              placeholder="You are a helpful assistant..."
              rows={5}
              className="font-mono text-sm"
            />
          </div>

          <div className="grid grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label htmlFor="model">Model</Label>
              <Input
                id="model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="gpt-4o-mini"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="strategy">Strategy</Label>
              <Input
                id="strategy"
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                placeholder="react"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="temperature">Temperature</Label>
              <Input
                id="temperature"
                type="number"
                step={0.1}
                min={0}
                max={2}
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
              />
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-3 pt-3 border-t">
            <Button onClick={handleCreate} className="flex-1">
              <Play className="mr-2 h-4 w-4" />
              Create & Open in Playground
            </Button>
            <Button
              variant="outline"
              onClick={() => router.push('/agents/templates')}
            >
              Cancel
            </Button>
          </div>

          {template && (
            <p className="text-xs text-muted-foreground text-center m-0">
              <Sparkles className="inline h-3 w-3 mr-1" />
              Template pre-fills the configuration — customize anything before creating.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
