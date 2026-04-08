'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { GuardrailConfig, AgentSummary } from '@/utils/types';
import { Card, Toggle, Skeleton, EmptyState } from '@sagecurator/ui';
import { HelpPanel } from '@/components/help-panel';

const GUARDRAIL_TYPES = ['pii', 'hallucination', 'content_filter'] as const;

const TYPE_LABELS: Record<string, string> = {
  pii: 'PII Detection',
  hallucination: 'Hallucination Guard',
  content_filter: 'Content Filter',
};

const TYPE_DESCRIPTIONS: Record<string, string> = {
  pii: 'Detects and redacts personal identifiable information (emails, SSNs, phone numbers, etc.)',
  hallucination: 'Flags potentially hallucinated or ungrounded claims in agent output',
  content_filter: 'Blocks harmful, toxic, or policy-violating content from agent responses',
};

export default function GuardrailsConfigPage() {
  const [configs, setConfigs] = useState<GuardrailConfig[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [cfgs, agts] = await Promise.all([
        adminApi.listGuardrailConfigs(),
        adminApi.listAgents(),
      ]);
      setConfigs(cfgs);
      setAgents(agts);
      setError(null);
    } catch {
      setError('Failed to load guardrail configs. Is the admin backend running?');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Build a lookup: agentName -> guardrailType -> config
  const configMap = new Map<string, Map<string, GuardrailConfig>>();
  for (const cfg of configs) {
    if (!configMap.has(cfg.agent_name)) configMap.set(cfg.agent_name, new Map());
    configMap.get(cfg.agent_name)!.set(cfg.guardrail_type, cfg);
  }

  // All agent names (from registry + any that have configs)
  const agentNames = [
    ...new Set([
      ...agents.map((a) => a.name),
      ...configs.map((c) => c.agent_name),
    ]),
  ].sort();

  async function handleToggle(agentName: string, guardrailType: string, enabled: boolean) {
    const key = `${agentName}:${guardrailType}`;
    setSaving(key);
    try {
      await adminApi.upsertGuardrailConfig(agentName, guardrailType, enabled);
      await fetchData();
    } catch {
      setError(`Failed to update ${guardrailType} for ${agentName}`);
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">Guardrails Configuration</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Enable or disable safety guardrails per agent. Changes take effect immediately.
      </p>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {/* Legend */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(250px,1fr))] gap-md mb-lg">
        {GUARDRAIL_TYPES.map((type) => (
          <Card key={type}>
            <h4 className="mt-0 mb-1 text-sm font-semibold">{TYPE_LABELS[type]}</h4>
            <p className="m-0 text-xs text-text-muted">{TYPE_DESCRIPTIONS[type]}</p>
          </Card>
        ))}
      </div>

      {/* Config table */}
      <Card>
        {loading ? (
          <Skeleton lines={5} />
        ) : agentNames.length === 0 ? (
          <EmptyState
            title="No Agents"
            description="No agents registered. Start the admin backend with agents to configure guardrails."
          />
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border">
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Agent</th>
                {GUARDRAIL_TYPES.map((type) => (
                  <th key={type} className="text-center py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">
                    {TYPE_LABELS[type]}
                  </th>
                ))}
                <th className="text-center py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Actions</th>
              </tr>
            </thead>
            <tbody>
              {agentNames.map((agentName) => {
                const agentConfigs = configMap.get(agentName);
                return (
                  <tr key={agentName} className="border-b border-border last:border-0">
                    <td className="py-2.5 px-3 font-medium">{agentName}</td>
                    {GUARDRAIL_TYPES.map((type) => {
                      const cfg = agentConfigs?.get(type);
                      const enabled = cfg?.enabled ?? false;
                      const key = `${agentName}:${type}`;
                      const isSaving = saving === key;
                      return (
                        <td key={type} className="py-2.5 px-3 text-center">
                          <div className={`inline-flex ${isSaving ? 'opacity-60' : ''}`}>
                            <Toggle
                              checked={enabled}
                              onChange={() => handleToggle(agentName, type, !enabled)}
                              label=""
                            />
                          </div>
                        </td>
                      );
                    })}
                    <td className="py-2.5 px-3 text-center">
                      {agentConfigs && agentConfigs.size > 0 && (
                        <span className="text-xs text-text-muted">
                          {agentConfigs.size} active
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <HelpPanel title="Guardrails">
        <h3>PII Detection</h3>
        <p>Scans agent input and output for personal identifiable information — emails, phone numbers, SSNs, credit card numbers. Detected PII is redacted before reaching the user.</p>
        <h3>Hallucination Guard</h3>
        <p>Compares agent output against retrieved context (RAG) to flag claims that are not grounded in source material. Helps maintain factual accuracy.</p>
        <h3>Content Filter</h3>
        <p>Blocks harmful, toxic, or policy-violating content from agent responses. Covers hate speech, violence, and inappropriate content categories.</p>
        <h3>How it works</h3>
        <p>Toggle guardrails per agent. Changes take effect immediately for all new requests. Existing in-flight requests are not affected.</p>
      </HelpPanel>
    </div>
  );
}
