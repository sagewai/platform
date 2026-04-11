'use client';

import { useEffect, useState, useCallback } from 'react';
import { PageLayout, Card, Button, Badge, FormField, TextInput, useToast } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type {
  Trigger,
  CreateTriggerRequest,
  ConnectorCatalogItem,
  AgentSummary,
} from '@/utils/types';
import {
  Plus,
  Trash2,
  Zap,
  Webhook,
  Radio,
  RefreshCw,
  ToggleLeft,
  ToggleRight,
  Plug,
  Bot,
  X,
} from 'lucide-react';

/* ─── Strategy badge ─── */

function StrategyBadge({ strategy }: { strategy: string }) {
  const config: Record<string, { icon: typeof Webhook; label: string; variant: 'info' | 'warning' | 'success' }> = {
    webhook: { icon: Webhook, label: 'Webhook', variant: 'info' },
    listener: { icon: Radio, label: 'Listener', variant: 'warning' },
    poller: { icon: RefreshCw, label: 'Poller', variant: 'success' },
  };
  const c = config[strategy] || { icon: Zap, label: strategy, variant: 'info' as const };
  const Icon = c.icon;
  return (
    <Badge variant={c.variant} className="text-[10px]">
      <Icon size={10} className="mr-1 inline" />
      {c.label}
    </Badge>
  );
}

/* ─── Trigger Card ─── */

function TriggerCard({
  trigger,
  connectors,
  onDelete,
  onToggle,
}: {
  trigger: Trigger;
  connectors: ConnectorCatalogItem[];
  onDelete: (id: string) => void;
  onToggle: (id: string, enabled: boolean) => void;
}) {
  const sourceConnector = connectors.find((c) => c.name === trigger.source);
  const filterEntries = Object.entries(trigger.filter || {}).filter(
    ([, v]) => v && (Array.isArray(v) ? v.length > 0 : true),
  );

  return (
    <div className={`border rounded-lg px-4 py-3 bg-bg-surface ${trigger.enabled ? 'border-border' : 'border-border opacity-60'}`}>
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <Zap size={16} className={trigger.enabled ? 'text-primary' : 'text-text-muted'} />
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-text-primary flex items-center gap-1">
                <Plug size={12} className="text-text-muted" />
                {sourceConnector?.display_name || trigger.source}
              </span>
              <span className="text-text-muted text-xs">&rarr;</span>
              <span className="text-sm font-medium text-text-primary flex items-center gap-1">
                <Bot size={12} className="text-text-muted" />
                {trigger.target}
              </span>
              <StrategyBadge strategy={trigger.strategy} />
              {trigger.strategy === 'poller' && trigger.poll_interval_seconds && (
                <span className="text-[10px] text-text-muted">every {trigger.poll_interval_seconds}s</span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-1 text-xs text-text-muted">
              <span>Action: <span className="font-medium">{trigger.action}</span></span>
              {filterEntries.length > 0 && (
                <span className="text-text-secondary">
                  | Filter: {filterEntries.map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join('; ')}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            onClick={() => onToggle(trigger.id, !trigger.enabled)}
            className="bg-transparent border-none cursor-pointer text-text-muted hover:text-primary transition-colors p-1"
            title={trigger.enabled ? 'Disable' : 'Enable'}
          >
            {trigger.enabled
              ? <ToggleRight size={20} className="text-success" />
              : <ToggleLeft size={20} />
            }
          </button>
          <button
            type="button"
            onClick={() => onDelete(trigger.id)}
            className="bg-transparent border-none cursor-pointer text-text-muted hover:text-error transition-colors p-1"
            title="Delete trigger"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

/* ─── Create Trigger Form ─── */

function CreateTriggerForm({
  connectors,
  agents,
  onSubmit,
  onCancel,
}: {
  connectors: ConnectorCatalogItem[];
  agents: AgentSummary[];
  onSubmit: (data: CreateTriggerRequest) => Promise<void>;
  onCancel: () => void;
}) {
  const [source, setSource] = useState('');
  const [strategy, setStrategy] = useState('');
  const [pollInterval, setPollInterval] = useState('60');
  const [target, setTarget] = useState('');
  const [action, setAction] = useState('chat');
  const [workflowYaml, setWorkflowYaml] = useState('');
  const [toolName, setToolName] = useState('');
  const [toolArguments, setToolArguments] = useState('');
  const [channels, setChannels] = useState('');
  const [eventTypes, setEventTypes] = useState('');
  const [senders, setSenders] = useState('');
  const [keywords, setKeywords] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Filter to connectors with event support
  const eventConnectors = connectors.filter(
    (c) => c.supports_webhook || c.supports_listener || c.supports_poller,
  );

  const selectedConnector = connectors.find((c) => c.name === source);

  // Available strategies based on selected connector
  const strategies: { value: string; label: string }[] = [];
  if (selectedConnector?.supports_webhook) strategies.push({ value: 'webhook', label: 'Webhook' });
  if (selectedConnector?.supports_listener) strategies.push({ value: 'listener', label: 'Listener' });
  if (selectedConnector?.supports_poller) strategies.push({ value: 'poller', label: 'Poller' });

  const canSubmit =
    source &&
    strategy &&
    target &&
    (action !== 'run_workflow' || workflowYaml.trim()) &&
    (action !== 'execute_tool' || toolName.trim());

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const filter: CreateTriggerRequest['filter'] = {};
      if (channels.trim()) filter.channels = channels.split(',').map((s) => s.trim());
      if (eventTypes.trim()) filter.event_types = eventTypes.split(',').map((s) => s.trim());
      if (senders.trim()) filter.senders = senders.split(',').map((s) => s.trim());
      if (keywords.trim()) filter.keywords = keywords.split(',').map((s) => s.trim());

      const context: Record<string, unknown> = {};
      if (action === 'run_workflow' && workflowYaml.trim()) {
        context.yaml = workflowYaml.trim();
      }
      if (action === 'execute_tool') {
        if (toolName.trim()) context.tool_name = toolName.trim();
        if (toolArguments.trim()) {
          try {
            context.tool_arguments = JSON.parse(toolArguments.trim());
          } catch {
            context.tool_arguments = {};
          }
        }
      }

      await onSubmit({
        source,
        strategy,
        poll_interval_seconds: strategy === 'poller' ? parseInt(pollInterval, 10) || 60 : undefined,
        filter: Object.keys(filter).length > 0 ? filter : undefined,
        target,
        action,
        context: Object.keys(context).length > 0 ? context : undefined,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Card>
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap size={16} className="text-primary" />
            <h3 className="text-base font-semibold m-0">Create Trigger</h3>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="bg-transparent border-none cursor-pointer text-text-muted hover:text-text-primary p-1"
          >
            <X size={16} />
          </button>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <FormField label="Source Connector">
            <select
              value={source}
              onChange={(e) => { setSource(e.target.value); setStrategy(''); }}
              className="w-full h-9 px-3 text-sm bg-bg-surface border border-border rounded-md text-text-primary"
            >
              <option value="">Select connector...</option>
              {eventConnectors.map((c) => (
                <option key={c.name} value={c.name}>{c.display_name}</option>
              ))}
            </select>
          </FormField>

          <FormField label="Strategy">
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              disabled={!source}
              className="w-full h-9 px-3 text-sm bg-bg-surface border border-border rounded-md text-text-primary disabled:opacity-50"
            >
              <option value="">Select strategy...</option>
              {strategies.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
          </FormField>
        </div>

        {strategy === 'poller' && (
          <FormField label="Poll Interval (seconds)" hint="How often to check for new events (min: 5)">
            <TextInput
              type="number"
              value={pollInterval}
              onChange={(e) => setPollInterval(e.target.value)}
              min={5}
              max={86400}
            />
          </FormField>
        )}

        <div className="grid grid-cols-2 gap-3">
          <FormField label="Target Agent">
            <select
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              className="w-full h-9 px-3 text-sm bg-bg-surface border border-border rounded-md text-text-primary"
            >
              <option value="">Select agent...</option>
              {agents.map((a) => (
                <option key={a.name} value={a.name}>{a.name}</option>
              ))}
            </select>
          </FormField>

          <FormField label="Action">
            <select
              value={action}
              onChange={(e) => setAction(e.target.value)}
              className="w-full h-9 px-3 text-sm bg-bg-surface border border-border rounded-md text-text-primary"
            >
              <option value="chat">Chat</option>
              <option value="run_workflow">Run Workflow</option>
              <option value="execute_tool">Execute Tool</option>
            </select>
          </FormField>
        </div>

        {/* Action-specific context */}
        {action === 'run_workflow' && (
          <FormField label="Workflow YAML" hint="The full YAML workflow definition to run when triggered">
            <textarea
              value={workflowYaml}
              onChange={(e) => setWorkflowYaml(e.target.value)}
              placeholder={"name: my-workflow\nsteps:\n  - agent: researcher\n    task: Investigate the event"}
              rows={6}
              className="w-full px-3 py-2 text-sm bg-bg-surface border border-border rounded-md text-text-primary font-mono resize-y"
            />
          </FormField>
        )}

        {action === 'execute_tool' && (
          <div className="grid grid-cols-2 gap-3">
            <FormField label="Tool Name" hint="Name of the tool to execute on the target agent">
              <TextInput
                value={toolName}
                onChange={(e) => setToolName(e.target.value)}
                placeholder="web_search"
              />
            </FormField>
            <FormField label="Tool Arguments (JSON)" hint="Optional JSON object of arguments">
              <TextInput
                value={toolArguments}
                onChange={(e) => setToolArguments(e.target.value)}
                placeholder='{"query": "{{event.text}}"}'
              />
            </FormField>
          </div>
        )}

        {/* Event filter */}
        <div>
          <span className="text-sm font-medium text-text-primary mb-2 block">Event Filter (optional)</span>
          <div className="grid grid-cols-2 gap-3">
            <FormField label="Channels" hint="Comma-separated">
              <TextInput
                value={channels}
                onChange={(e) => setChannels(e.target.value)}
                placeholder="general, support"
              />
            </FormField>
            <FormField label="Event Types" hint="Comma-separated">
              <TextInput
                value={eventTypes}
                onChange={(e) => setEventTypes(e.target.value)}
                placeholder="message, mention"
              />
            </FormField>
            <FormField label="Senders" hint="Comma-separated">
              <TextInput
                value={senders}
                onChange={(e) => setSenders(e.target.value)}
                placeholder="user@example.com"
              />
            </FormField>
            <FormField label="Keywords" hint="Comma-separated">
              <TextInput
                value={keywords}
                onChange={(e) => setKeywords(e.target.value)}
                placeholder="urgent, help"
              />
            </FormField>
          </div>
        </div>

        <div className="flex items-center gap-2 pt-2 border-t border-border">
          <Button size="sm" onClick={handleSubmit} disabled={!canSubmit || submitting}>
            {submitting ? 'Creating...' : 'Create Trigger'}
          </Button>
          <Button size="sm" variant="secondary" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </div>
    </Card>
  );
}

/* ─── Main Page ─── */

export default function TriggersPage() {
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [connectors, setConnectors] = useState<ConnectorCatalogItem[]>([]);
  const [agents, setAgents] = useState<AgentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const { toast } = useToast();

  const loadData = useCallback(async () => {
    try {
      const [t, c, a] = await Promise.all([
        adminApi.listTriggers(),
        adminApi.listConnectors(),
        adminApi.listAgents(),
      ]);
      setTriggers(t);
      setConnectors(c);
      setAgents(a);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleCreate = async (data: CreateTriggerRequest) => {
    try {
      await adminApi.createTrigger(data);
      toast('success', 'Trigger created');
      setShowForm(false);
      await loadData();
    } catch {
      toast('error', 'Failed to create trigger');
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await adminApi.deleteTrigger(id);
      toast('success', 'Trigger deleted');
      await loadData();
    } catch {
      toast('error', 'Failed to delete trigger');
    }
  };

  const handleToggle = async (id: string, enabled: boolean) => {
    try {
      if (enabled) {
        await adminApi.enableTrigger(id);
      } else {
        await adminApi.disableTrigger(id);
      }
      setTriggers((prev) =>
        prev.map((t) => (t.id === id ? { ...t, enabled } : t)),
      );
      toast('success', enabled ? 'Trigger enabled' : 'Trigger disabled');
    } catch {
      toast('error', 'Failed to update trigger');
    }
  };

  const eventConnectors = connectors.filter(
    (c) => c.supports_webhook || c.supports_listener || c.supports_poller,
  );

  return (
    <PageLayout
      title="Triggers"
      description={
        loading
          ? 'Loading triggers...'
          : triggers.length > 0
            ? `${triggers.length} trigger${triggers.length !== 1 ? 's' : ''} configured — routing connector events to agents.`
            : 'Automatically route events from connectors to agents via webhooks, polling, or real-time listeners.'
      }
      actions={
        !showForm ? (
          <Button size="sm" onClick={() => setShowForm(true)} disabled={eventConnectors.length === 0}>
            <Plus size={14} className="mr-1" /> Create Trigger
          </Button>
        ) : undefined
      }
    >
      {showForm && (
        <div className="mb-xl">
          <CreateTriggerForm
            connectors={connectors}
            agents={agents}
            onSubmit={handleCreate}
            onCancel={() => setShowForm(false)}
          />
        </div>
      )}

      {triggers.length > 0 && (
        <div className="flex flex-col gap-2">
          {triggers.map((trigger) => (
            <TriggerCard
              key={trigger.id}
              trigger={trigger}
              connectors={connectors}
              onDelete={handleDelete}
              onToggle={handleToggle}
            />
          ))}
        </div>
      )}

      {!loading && triggers.length === 0 && !showForm && (
        <Card>
          <div className="text-center py-8 text-text-muted">
            <Zap size={32} className="mx-auto mb-3 opacity-40" />
            <p className="text-sm font-medium text-text-primary">No triggers configured yet</p>
            <p className="text-xs mt-2 max-w-[28rem] mx-auto leading-relaxed">
              Triggers automatically wire connector events to your agents.
              When an event occurs (e.g. a Slack message, a new email, or a webhook call),
              the trigger routes it to an agent for processing.
            </p>
            <div className="mt-4 flex flex-col items-center gap-2 text-xs text-text-secondary">
              <div className="flex items-center gap-4">
                <span className="flex items-center gap-1"><Webhook size={12} /> <strong>Webhook</strong> — receive HTTP callbacks</span>
                <span className="flex items-center gap-1"><Radio size={12} /> <strong>Listener</strong> — real-time event streams</span>
                <span className="flex items-center gap-1"><RefreshCw size={12} /> <strong>Poller</strong> — periodic checks</span>
              </div>
            </div>
            {eventConnectors.length > 0 ? (
              <Button size="sm" className="mt-4" onClick={() => setShowForm(true)}>
                <Plus size={14} className="mr-1" /> Create Your First Trigger
              </Button>
            ) : (
              <p className="text-xs mt-4 text-warning">
                No connectors with event support found. Configure a connector with webhook, listener,
                or poller support in{' '}
                <a href="/settings/services" className="text-primary hover:underline">Settings → Connectors</a>{' '}
                first.
              </p>
            )}
          </div>
        </Card>
      )}
    </PageLayout>
  );
}
