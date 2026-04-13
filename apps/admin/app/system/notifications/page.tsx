'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import {
  Card, Button, Badge, Skeleton, EmptyState, useToast, TextInput, FormField,
} from '@/components/ui/legacy';
import { Mail, MessageSquare, Bell, Trash2, Play, Check, X } from 'lucide-react';

/* ─── Types ─── */

interface ChannelConfig {
  id: string;
  channel_type: string;
  enabled: boolean;
  project_id?: string | null;
  // Email (API-based — Resend, SendGrid, Postmark)
  email_provider?: string;
  email_api_key?: string;
  email_from?: string;
  email?: string;           // recipient
  // Slack
  webhook_url?: string;
  slack_channel?: string;
}

interface TriggerRouting {
  id: string;
  trigger: string;
  channel_type: string;
  enabled: boolean;
  project_id?: string | null;
}

interface NotificationHistory {
  id: string;
  trigger: string;
  title: string;
  body?: string;
  severity: string;
  channel_type: string;
  delivered: boolean;
  error?: string | null;
  created_at: string;
  agent_name?: string | null;
}

/* ─── Channel icons ─── */

const CHANNEL_ICONS: Record<string, typeof Mail> = {
  email: Mail,
  slack: MessageSquare,
  in_app: Bell,
};

const CHANNEL_LABELS: Record<string, string> = {
  email: 'Email (API)',
  slack: 'Slack Webhook',
  in_app: 'In-App Alerts',
};

const TRIGGER_LABELS: Record<string, string> = {
  budget_warning: 'Budget Warning',
  budget_exceeded: 'Budget Exceeded',
  budget_throttled: 'Budget Throttled',
  workflow_failed: 'Workflow Failed',
  approval_requested: 'Approval Requested',
};

const SEVERITY_COLORS: Record<string, 'red' | 'yellow' | 'blue' | 'green'> = {
  critical: 'red',
  warning: 'yellow',
  info: 'blue',
};

/* ─── Main page ─── */

export default function NotificationsPage() {
  const [activeTab, setActiveTab] = useState<'channels' | 'triggers' | 'history'>('channels');

  return (
    <div className="space-y-lg">
      <div>
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)]">
          Notifications
        </h1>
        <p className="text-text-secondary mt-1">
          Configure how you receive alerts for budget warnings, workflow failures, and system events.
        </p>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-bg-secondary rounded-lg p-1 w-fit">
        {(['channels', 'triggers', 'history'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 rounded-md text-sm font-medium capitalize transition-colors ${
              activeTab === tab
                ? 'bg-bg-primary text-text-primary shadow-sm'
                : 'text-text-secondary hover:text-text-primary'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {activeTab === 'channels' && <ChannelsSection />}
      {activeTab === 'triggers' && <TriggersSection />}
      {activeTab === 'history' && <HistorySection />}
    </div>
  );
}

/* ─── Channels Section ─── */

function ChannelsSection() {
  const [channels, setChannels] = useState<ChannelConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingType, setEditingType] = useState<string | null>(null);
  const { toast } = useToast();

  // Form state for email (API-key based)
  const [emailProvider, setEmailProvider] = useState('resend');
  const [emailApiKey, setEmailApiKey] = useState('');
  const [fromAddress, setFromAddress] = useState('');
  const [toAddresses, setToAddresses] = useState('');

  // Form state for slack
  const [webhookUrl, setWebhookUrl] = useState('');
  const [slackChannel, setSlackChannel] = useState('');

  const fetchChannels = useCallback(async () => {
    try {
      const data = await adminApi.listNotificationChannels();
      setChannels(data as unknown as ChannelConfig[]);
    } catch {
      // empty — no channels configured yet
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchChannels(); }, [fetchChannels]);

  function getChannelConfig(type: string): ChannelConfig | undefined {
    return channels.find((c) => c.channel_type === type);
  }

  function startEditing(type: string) {
    const existing = getChannelConfig(type);
    if (type === 'email' && existing) {
      setEmailProvider(existing.email_provider ?? 'resend');
      setEmailApiKey(existing.email_api_key ?? '');
      setFromAddress(existing.email_from ?? '');
      setToAddresses(existing.email ?? '');
    } else if (type === 'slack' && existing) {
      setWebhookUrl(existing.webhook_url ?? '');
      setSlackChannel(existing.slack_channel ?? '');
    }
    setEditingType(type);
  }

  async function handleSave(type: string) {
    try {
      if (type === 'email') {
        await adminApi.saveNotificationChannel({
          channel_type: 'email',
          enabled: true,
          email_provider: emailProvider,
          email_api_key: emailApiKey,
          email_from: fromAddress,
          email: toAddresses,
        });
      } else if (type === 'slack') {
        await adminApi.saveNotificationChannel({
          channel_type: 'slack',
          enabled: true,
          webhook_url: webhookUrl,
          slack_channel: slackChannel || undefined,
        });
      } else {
        await adminApi.saveNotificationChannel({
          channel_type: 'in_app',
          enabled: true,
        });
      }
      toast('success', 'Channel saved');
      setEditingType(null);
      fetchChannels();
    } catch {
      toast('error', 'Failed to save channel');
    }
  }

  async function handleTest(type: string) {
    try {
      const result = await adminApi.testNotification({ channel_type: type as 'email' | 'slack' | 'in_app' });
      if (result.sent) {
        toast('success', 'Test notification sent successfully');
      } else {
        toast('error', result.error || 'Test notification failed');
      }
    } catch {
      toast('error', 'Failed to send test');
    }
  }

  async function handleDelete(id: string) {
    try {
      await adminApi.deleteNotificationChannel(id);
      toast('success', 'Channel deleted');
      fetchChannels();
    } catch {
      toast('error', 'Failed to delete channel');
    }
  }

  if (loading) return <Skeleton className="h-48" />;

  return (
    <div className="grid gap-lg md:grid-cols-3">
      {(['email', 'slack', 'in_app'] as const).map((type) => {
        const Icon = CHANNEL_ICONS[type];
        const config = getChannelConfig(type);
        const isEditing = editingType === type;

        return (
          <Card key={type} className="p-lg">
            <div className="flex items-center justify-between mb-md">
              <div className="flex items-center gap-2">
                <Icon size={18} className="text-text-secondary" />
                <h3 className="font-semibold">{CHANNEL_LABELS[type]}</h3>
              </div>
              {config && (
                <Badge variant={config.enabled ? 'success' : 'warning'}>
                  {config.enabled ? 'Active' : 'Disabled'}
                </Badge>
              )}
            </div>

            {!isEditing && !config && type !== 'in_app' && (
              <div className="text-center py-md">
                <p className="text-text-secondary text-sm mb-md">Not configured</p>
                <Button size="sm" onClick={() => startEditing(type)}>Configure</Button>
              </div>
            )}

            {!isEditing && type === 'in_app' && (
              <div className="text-sm text-text-secondary">
                <p>In-app notifications are always enabled. Alerts appear in the Execution Monitor SSE stream.</p>
              </div>
            )}

            {!isEditing && config && type === 'email' && (
              <div className="space-y-2 text-sm">
                <p><span className="text-text-secondary">Provider:</span> {config.email_provider ?? 'Not set'}</p>
                <p><span className="text-text-secondary">From:</span> {config.from_address}</p>
                <p><span className="text-text-secondary">To:</span> {(config.to_addresses ?? []).join(', ')}</p>
                <div className="flex gap-2 mt-md">
                  <Button size="sm" variant="secondary" onClick={() => startEditing(type)}>Edit</Button>
                  <Button size="sm" variant="secondary" onClick={() => handleTest(type)}>
                    <Play size={14} className="mr-1" /> Test
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => handleDelete(config.id)}>
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>
            )}

            {!isEditing && config && type === 'slack' && (
              <div className="space-y-2 text-sm">
                <p><span className="text-text-secondary">Webhook:</span> {config.webhook_url ? '***configured***' : 'not set'}</p>
                {config.slack_channel && <p><span className="text-text-secondary">Channel:</span> {config.slack_channel}</p>}
                <div className="flex gap-2 mt-md">
                  <Button size="sm" variant="secondary" onClick={() => startEditing(type)}>Edit</Button>
                  <Button size="sm" variant="secondary" onClick={() => handleTest(type)}>
                    <Play size={14} className="mr-1" /> Test
                  </Button>
                  <Button size="sm" variant="danger" onClick={() => handleDelete(config.id)}>
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>
            )}

            {/* Email editing form */}
            {isEditing && type === 'email' && (
              <div className="space-y-3">
                <FormField label="Provider">
                  <select value={emailProvider} onChange={(e) => setEmailProvider(e.target.value)} className="w-full rounded-md border border-border bg-bg-surface px-3 py-2 text-sm text-text-primary">
                    <option value="resend">Resend</option>
                    <option value="sendgrid">SendGrid</option>
                    <option value="postmark">Postmark</option>
                  </select>
                </FormField>
                <FormField label="API Key" hint={emailProvider === 'resend' ? 'Starts with re_' : emailProvider === 'sendgrid' ? 'Starts with SG.' : 'Server API token'}><TextInput type="password" value={emailApiKey} onChange={(e) => setEmailApiKey(e.target.value)} placeholder={emailProvider === 'resend' ? 're_...' : emailProvider === 'sendgrid' ? 'SG...' : 'your-server-token'} /></FormField>
                <FormField label="From Address"><TextInput value={fromAddress} onChange={(e) => setFromAddress(e.target.value)} placeholder="notifications@yourdomain.com" /></FormField>
                <FormField label="To Address"><TextInput value={toAddresses} onChange={(e) => setToAddresses(e.target.value)} placeholder="admin@yourdomain.com" /></FormField>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => handleSave('email')}>Save</Button>
                  <Button size="sm" variant="secondary" onClick={() => setEditingType(null)}>Cancel</Button>
                </div>
              </div>
            )}

            {/* Slack editing form */}
            {isEditing && type === 'slack' && (
              <div className="space-y-3">
                <FormField label="Webhook URL"><TextInput value={webhookUrl} onChange={(e) => setWebhookUrl(e.target.value)} placeholder="https://hooks.slack.com/services/..." /></FormField>
                <FormField label="Channel (optional)"><TextInput value={slackChannel} onChange={(e) => setSlackChannel(e.target.value)} placeholder="#alerts" /></FormField>
                <div className="flex gap-2">
                  <Button size="sm" onClick={() => handleSave('slack')}>Save</Button>
                  <Button size="sm" variant="secondary" onClick={() => setEditingType(null)}>Cancel</Button>
                </div>
              </div>
            )}
          </Card>
        );
      })}
    </div>
  );
}

/* ─── Triggers Section ─── */

function TriggersSection() {
  const [triggers, setTriggers] = useState<TriggerRouting[]>([]);
  const [loading, setLoading] = useState(true);
  const { toast } = useToast();

  const fetchTriggers = useCallback(async () => {
    try {
      const data = await adminApi.listNotificationTriggers();
      setTriggers(data as unknown as TriggerRouting[]);
    } catch {
      // empty
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchTriggers(); }, [fetchTriggers]);

  const triggerNames = Object.keys(TRIGGER_LABELS);
  const channelTypes = ['email', 'slack', 'in_app'];

  function isEnabled(trigger: string, channelType: string): boolean {
    const match = triggers.find((t) => t.trigger === trigger && t.channel_type === channelType);
    return match ? match.enabled : false;
  }

  async function toggleTrigger(trigger: string, channelType: string) {
    const existing = triggers.find((t) => t.trigger === trigger && t.channel_type === channelType);
    try {
      if (existing?.enabled) {
        // If it's a default (id starts with "default:"), we need to save a disabled entry
        await adminApi.saveNotificationTrigger({
          trigger,
          channel_type: channelType,
          enabled: false,
        });
      } else {
        await adminApi.saveNotificationTrigger({
          trigger,
          channel_type: channelType,
          enabled: true,
        });
      }
      fetchTriggers();
    } catch {
      toast('error', 'Failed to update trigger routing');
    }
  }

  if (loading) return <Skeleton className="h-48" />;

  return (
    <Card className="p-lg">
      <h3 className="font-semibold mb-md">Trigger Routing</h3>
      <p className="text-sm text-text-secondary mb-lg">
        Configure which notification channels are activated for each event type.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-2 pr-4 font-medium text-text-secondary">Event</th>
              {channelTypes.map((ct) => (
                <th key={ct} className="text-center py-2 px-4 font-medium text-text-secondary">
                  {CHANNEL_LABELS[ct]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {triggerNames.map((trigger) => (
              <tr key={trigger} className="border-b border-border/50">
                <td className="py-3 pr-4 font-medium">{TRIGGER_LABELS[trigger]}</td>
                {channelTypes.map((ct) => {
                  const enabled = isEnabled(trigger, ct);
                  return (
                    <td key={ct} className="text-center py-3 px-4">
                      <button
                        onClick={() => toggleTrigger(trigger, ct)}
                        className={`w-5 h-5 rounded border-2 inline-flex items-center justify-center transition-colors ${
                          enabled
                            ? 'bg-primary border-primary text-white'
                            : 'border-border hover:border-text-secondary'
                        }`}
                        aria-label={`${enabled ? 'Disable' : 'Enable'} ${TRIGGER_LABELS[trigger]} for ${CHANNEL_LABELS[ct]}`}
                      >
                        {enabled && <Check size={12} strokeWidth={3} />}
                      </button>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/* ─── History Section ─── */

function HistorySection() {
  const [history, setHistory] = useState<NotificationHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const limit = 25;

  const fetchHistory = useCallback(async () => {
    setLoading(true);
    try {
      const data = await adminApi.listNotificationHistory({ limit, offset });
      setHistory(data as unknown as NotificationHistory[]);
    } catch {
      // empty
    } finally {
      setLoading(false);
    }
  }, [offset]);

  useEffect(() => { fetchHistory(); }, [fetchHistory]);

  if (loading) return <Skeleton className="h-48" />;

  if (history.length === 0) {
    return (
      <Card className="p-lg">
        <EmptyState
          title="No notifications yet"
          description="Notification history will appear here once budget alerts or system events trigger notifications."
        />
      </Card>
    );
  }

  return (
    <Card className="p-lg">
      <h3 className="font-semibold mb-md">Notification History</h3>

      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border">
            <th className="py-2 px-3 text-left text-xs text-text-muted uppercase">Time</th>
            <th className="py-2 px-3 text-left text-xs text-text-muted uppercase">Trigger</th>
            <th className="py-2 px-3 text-left text-xs text-text-muted uppercase">Title</th>
            <th className="py-2 px-3 text-left text-xs text-text-muted uppercase">Channel</th>
            <th className="py-2 px-3 text-left text-xs text-text-muted uppercase">Severity</th>
            <th className="py-2 px-3 text-left text-xs text-text-muted uppercase">Status</th>
          </tr>
        </thead>
        <tbody>
          {history.map((h) => (
            <tr key={h.id} className="border-b border-border last:border-0 hover:bg-bg-subtle">
              <td className="py-2 px-3 text-text-muted text-xs">{new Date(h.created_at).toLocaleString()}</td>
              <td className="py-2 px-3">{TRIGGER_LABELS[h.trigger] ?? h.trigger}</td>
              <td className="py-2 px-3">{h.title}</td>
              <td className="py-2 px-3">{CHANNEL_LABELS[h.channel_type] ?? h.channel_type}</td>
              <td className="py-2 px-3"><Badge variant={h.severity === 'critical' ? 'error' : h.severity === 'warning' ? 'warning' : 'info'}>{h.severity}</Badge></td>
              <td className="py-2 px-3">{h.delivered ? <Badge variant="success">Delivered</Badge> : <Badge variant="error">Failed</Badge>}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex justify-between mt-md">
        <Button
          size="sm"
          variant="secondary"
          disabled={offset === 0}
          onClick={() => setOffset(Math.max(0, offset - limit))}
        >
          Previous
        </Button>
        <Button
          size="sm"
          variant="secondary"
          disabled={history.length < limit}
          onClick={() => setOffset(offset + limit)}
        >
          Next
        </Button>
      </div>
    </Card>
  );
}
