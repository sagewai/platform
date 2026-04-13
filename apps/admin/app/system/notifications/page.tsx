'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import {
  Card, Button, Badge, Skeleton, EmptyState, useToast, TextInput, FormField,
} from '@/components/ui/legacy';
import { Mail, MessageSquare, Bell, Trash2, Play, Check, X } from 'lucide-react';
import { ProjectBadge } from '@/components/project-badge';
import { useProject } from '@/utils/project-context';

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
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] flex items-center">
          Notifications <ProjectBadge />
        </h1>
        <p className="text-text-secondary mt-1">
          Configure how you receive alerts for budget warnings, workflow failures, and system events.
          Channels are scoped to the currently selected project.
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
  const [editingId, setEditingId] = useState<string | null>(null);
  const [addingType, setAddingType] = useState<string | null>(null);
  const { toast } = useToast();

  // Form state
  const [formName, setFormName] = useState('');
  const [formWebhookUrl, setFormWebhookUrl] = useState('');
  const [formSlackChannel, setFormSlackChannel] = useState('');
  const [formEmailProvider, setFormEmailProvider] = useState('resend');
  const [formEmailApiKey, setFormEmailApiKey] = useState('');
  const [formEmailFrom, setFormEmailFrom] = useState('');
  const [formEmailTo, setFormEmailTo] = useState('');

  const fetchChannels = useCallback(async () => {
    try {
      const data = await adminApi.listNotificationChannels();
      setChannels(data as unknown as ChannelConfig[]);
    } catch {
      // empty
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchChannels(); }, [fetchChannels]);

  function resetForm() {
    setFormName(''); setFormWebhookUrl(''); setFormSlackChannel('');
    setFormEmailProvider('resend'); setFormEmailApiKey(''); setFormEmailFrom(''); setFormEmailTo('');
    setEditingId(null); setAddingType(null);
  }

  function startAdd(type: string) {
    resetForm();
    setAddingType(type);
  }

  function startEdit(ch: ChannelConfig) {
    resetForm();
    setEditingId(ch.id);
    setFormName((ch as unknown as Record<string, string>).name ?? '');
    if (ch.channel_type === 'slack') {
      setFormWebhookUrl(ch.webhook_url ?? '');
      setFormSlackChannel(ch.slack_channel ?? '');
    } else if (ch.channel_type === 'email') {
      setFormEmailProvider(ch.email_provider ?? 'resend');
      setFormEmailApiKey(ch.email_api_key ?? '');
      setFormEmailFrom(ch.email_from ?? '');
      setFormEmailTo(ch.email ?? '');
    }
  }

  async function handleSave(type: string) {
    try {
      const payload: Record<string, unknown> = { channel_type: type, enabled: true, name: formName || undefined };
      if (type === 'slack') {
        payload.webhook_url = formWebhookUrl;
        payload.slack_channel = formSlackChannel || undefined;
      } else if (type === 'email') {
        payload.email_provider = formEmailProvider;
        payload.email_api_key = formEmailApiKey;
        payload.email_from = formEmailFrom;
        payload.email = formEmailTo;
      }
      if (editingId) payload.id = editingId;
      await adminApi.saveNotificationChannel(payload);
      toast('success', 'Channel saved');
      resetForm();
      fetchChannels();
    } catch {
      toast('error', 'Failed to save channel');
    }
  }

  async function handleTest(type: string) {
    try {
      const result = await adminApi.testNotification({ channel_type: type as 'email' | 'slack' | 'in_app' });
      if (result.sent) {
        toast('success', 'Test notification sent');
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
      toast('success', 'Channel removed');
      fetchChannels();
    } catch {
      toast('error', 'Failed to delete channel');
    }
  }

  if (loading) return <Skeleton className="h-48" />;

  const slackChannels = channels.filter((c) => c.channel_type === 'slack');
  const emailChannels = channels.filter((c) => c.channel_type === 'email');
  const isAddingSlack = addingType === 'slack';
  const isAddingEmail = addingType === 'email';

  return (
    <div className="space-y-lg">
      {/* ── Slack Channels ── */}
      <Card className="p-lg">
        <div className="flex items-center justify-between mb-md">
          <div className="flex items-center gap-2">
            <MessageSquare size={18} className="text-text-secondary" />
            <h3 className="font-semibold">Slack Channels</h3>
            <Badge variant="default">{slackChannels.length}</Badge>
          </div>
          <Button size="sm" onClick={() => startAdd('slack')}>+ Add Channel</Button>
        </div>

        {slackChannels.length === 0 && !isAddingSlack && (
          <p className="text-sm text-text-muted py-md text-center">No Slack channels configured. Add one to receive notifications.</p>
        )}

        <div className="space-y-2">
          {slackChannels.map((ch) => (
            <div key={ch.id} className="flex items-center justify-between px-4 py-3 bg-bg-subtle rounded-lg">
              <div className="min-w-0">
                <div className="text-sm font-medium text-text-primary truncate">
                  {ch.slack_channel || 'Default channel'}
                </div>
                <div className="text-xs text-text-muted truncate">
                  {ch.webhook_url ? `${ch.webhook_url.slice(0, 40)}...` : 'No webhook'}
                </div>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <Button size="sm" variant="ghost" onClick={() => handleTest('slack')} title="Test">
                  <Play size={14} />
                </Button>
                <Button size="sm" variant="ghost" onClick={() => startEdit(ch)} title="Edit">
                  Edit
                </Button>
                <Button size="sm" variant="ghost" onClick={() => handleDelete(ch.id)} title="Remove" className="text-error hover:text-error">
                  <Trash2 size={14} />
                </Button>
              </div>
            </div>
          ))}
        </div>

        {/* Add/Edit Slack form */}
        {(isAddingSlack || (editingId && channels.find(c => c.id === editingId)?.channel_type === 'slack')) && (
          <div className="mt-md p-4 border border-border rounded-lg space-y-3">
            <FormField label="Channel name (e.g., #alerts, #agent-runs)">
              <TextInput value={formSlackChannel} onChange={(e) => setFormSlackChannel(e.target.value)} placeholder="#alerts" />
            </FormField>
            <FormField label="Webhook URL">
              <TextInput value={formWebhookUrl} onChange={(e) => setFormWebhookUrl(e.target.value)} placeholder="https://hooks.slack.com/services/..." />
            </FormField>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => handleSave('slack')}>Save</Button>
              <Button size="sm" variant="secondary" onClick={resetForm}>Cancel</Button>
            </div>
          </div>
        )}
      </Card>

      {/* ── Email ── */}
      <Card className="p-lg">
        <div className="flex items-center justify-between mb-md">
          <div className="flex items-center gap-2">
            <Mail size={18} className="text-text-secondary" />
            <h3 className="font-semibold">Email (API)</h3>
            {emailChannels.length > 0 && <Badge variant="success">Active</Badge>}
          </div>
          {emailChannels.length === 0 && (
            <Button size="sm" onClick={() => startAdd('email')}>Configure</Button>
          )}
        </div>

        {emailChannels.length === 0 && !isAddingEmail && (
          <p className="text-sm text-text-muted py-md text-center">No email provider configured.</p>
        )}

        {emailChannels.map((ch) => (
          <div key={ch.id} className="space-y-2 text-sm">
            <p><span className="text-text-secondary">Provider:</span> {ch.email_provider ?? 'Not set'}</p>
            <p><span className="text-text-secondary">API Key:</span> {ch.email_api_key ? '***configured***' : 'not set'}</p>
            <p><span className="text-text-secondary">From:</span> {ch.email_from || 'default'}</p>
            <p><span className="text-text-secondary">To:</span> {ch.email || 'not set'}</p>
            <div className="flex gap-2 mt-md">
              <Button size="sm" variant="secondary" onClick={() => startEdit(ch)}>Edit</Button>
              <Button size="sm" variant="secondary" onClick={() => handleTest('email')}>
                <Play size={14} className="mr-1" /> Test
              </Button>
              <Button size="sm" variant="ghost" onClick={() => handleDelete(ch.id)} className="text-error hover:text-error">
                <Trash2 size={14} />
              </Button>
            </div>
          </div>
        ))}

        {/* Add/Edit Email form */}
        {(isAddingEmail || (editingId && channels.find(c => c.id === editingId)?.channel_type === 'email')) && (
          <div className="mt-md p-4 border border-border rounded-lg space-y-3">
            <FormField label="Provider">
              <select value={formEmailProvider} onChange={(e) => setFormEmailProvider(e.target.value)} className="w-full rounded-md border border-border bg-bg-surface px-3 py-2 text-sm text-text-primary">
                <option value="resend">Resend</option>
                <option value="sendgrid">SendGrid</option>
                <option value="postmark">Postmark</option>
              </select>
            </FormField>
            <FormField label="API Key" hint={formEmailProvider === 'resend' ? 'Starts with re_' : formEmailProvider === 'sendgrid' ? 'Starts with SG.' : 'Server API token'}>
              <TextInput type="password" value={formEmailApiKey} onChange={(e) => setFormEmailApiKey(e.target.value)} placeholder={formEmailProvider === 'resend' ? 're_...' : formEmailProvider === 'sendgrid' ? 'SG...' : 'your-server-token'} />
            </FormField>
            <FormField label="From Address"><TextInput value={formEmailFrom} onChange={(e) => setFormEmailFrom(e.target.value)} placeholder="notifications@yourdomain.com" /></FormField>
            <FormField label="To Address"><TextInput value={formEmailTo} onChange={(e) => setFormEmailTo(e.target.value)} placeholder="admin@yourdomain.com" /></FormField>
            <div className="flex gap-2">
              <Button size="sm" onClick={() => handleSave('email')}>Save</Button>
              <Button size="sm" variant="secondary" onClick={resetForm}>Cancel</Button>
            </div>
          </div>
        )}
      </Card>

      {/* ── In-App Alerts ── */}
      <Card className="p-lg">
        <div className="flex items-center gap-2 mb-md">
          <Bell size={18} className="text-text-secondary" />
          <h3 className="font-semibold">In-App Alerts</h3>
          <Badge variant="success">Always On</Badge>
        </div>
        <p className="text-sm text-text-secondary">
          In-app notifications are always enabled. Alerts appear in the Execution Monitor SSE stream.
        </p>
      </Card>
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
