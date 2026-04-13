'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { BillingPlan, BillingSubscription, BillingUsage, BillingInvoice } from '@/utils/types';
import { Card, Button, Badge, Skeleton, useToast } from '@/components/ui/legacy';
import {
  CreditCard,
  Zap,
  HardDrive,
  Server,
  Activity,
  ExternalLink,
  FileText,
  Check,
  ArrowRight,
} from 'lucide-react';

/* ─── Demo fallback data ─── */

const DEMO_SUBSCRIPTION: BillingSubscription = {
  plan: 'pro',
  status: 'active',
  current_period_end: '2026-04-30T00:00:00Z',
  cancel_at_period_end: false,
};

const DEMO_USAGE: BillingUsage = {
  period_start: '2026-03-01',
  period_end: '2026-03-31',
  agent_runs: 847,
  api_calls: 12450,
  storage_used_gb: 2.3,
  workers_active: 4,
  connectors_active: 7,
};

const DEMO_PLANS: BillingPlan[] = [
  {
    id: 'free',
    name: 'Free',
    price_monthly: 0,
    features: {
      workers: 1,
      connectors: 5,
      agent_runs_monthly: 100,
      storage_gb: 1,
      fleet: false,
      premium_support: false,
    },
  },
  {
    id: 'pro',
    name: 'Pro',
    price_monthly: 49,
    stripe_price_id: 'price_pro_monthly',
    features: {
      workers: 10,
      connectors: 18,
      agent_runs_monthly: 10000,
      storage_gb: 50,
      fleet: true,
      premium_support: false,
    },
  },
  {
    id: 'enterprise',
    name: 'Enterprise',
    price_monthly: null,
    features: {
      workers: -1,
      connectors: -1,
      agent_runs_monthly: -1,
      storage_gb: -1,
      fleet: true,
      premium_support: true,
    },
  },
];

const DEMO_INVOICES: BillingInvoice[] = [
  { id: 'inv_001', date: '2026-03-01', amount: 49.0, status: 'paid', pdf_url: '#' },
  { id: 'inv_002', date: '2026-02-01', amount: 49.0, status: 'paid', pdf_url: '#' },
  { id: 'inv_003', date: '2026-01-01', amount: 49.0, status: 'paid', pdf_url: '#' },
];

/* ─── Helpers ─── */

function featureLabel(key: string): string {
  const labels: Record<string, string> = {
    workers: 'Workers',
    connectors: 'Connectors',
    agent_runs_monthly: 'Agent Runs / mo',
    storage_gb: 'Storage',
    fleet: 'Fleet Management',
    premium_support: 'Premium Support',
  };
  return labels[key] ?? key;
}

function featureValue(key: string, val: number | boolean): string {
  if (typeof val === 'boolean') return val ? 'Included' : '--';
  if (val === -1) return 'Unlimited';
  if (key === 'storage_gb') return `${val} GB`;
  return val.toLocaleString();
}

function statusVariant(status: string): 'success' | 'warning' | 'error' | 'info' | 'default' {
  switch (status) {
    case 'active':
    case 'paid':
      return 'success';
    case 'trialing':
      return 'info';
    case 'past_due':
      return 'warning';
    case 'canceled':
    case 'unpaid':
      return 'error';
    default:
      return 'default';
  }
}

/* ─── Usage meter ─── */

function UsageMeter({
  label,
  icon: Icon,
  used,
  limit,
  unit,
}: {
  label: string;
  icon: typeof Activity;
  used: number;
  limit: number;
  unit?: string;
}) {
  const pct = limit > 0 ? Math.min((used / limit) * 100, 100) : 0;
  const barColor =
    pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-yellow-500' : 'bg-primary';

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-sm">
        <span className="flex items-center gap-2 text-text-on-dark/70">
          <Icon size={14} />
          {label}
        </span>
        <span className="text-text-on-dark/90 font-mono text-xs">
          {typeof used === 'number' && used % 1 !== 0 ? used.toFixed(1) : used.toLocaleString()}
          {' / '}
          {limit === -1 ? 'Unlimited' : `${limit.toLocaleString()}${unit ? ` ${unit}` : ''}`}
        </span>
      </div>
      <div className="h-2 rounded-full bg-bg-subtle overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/* ─── Main page ─── */

export default function BillingPage() {
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [subscription, setSubscription] = useState<BillingSubscription | null>(null);
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [invoices, setInvoices] = useState<BillingInvoice[]>([]);
  const [loading, setLoading] = useState(true);
  const { toast } = useToast();

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [plansRes, subRes, usageRes, invRes] = await Promise.allSettled([
        adminApi.listBillingPlans(),
        adminApi.getBillingSubscription(),
        adminApi.getBillingUsage(),
        adminApi.listBillingInvoices(),
      ]);
      if (plansRes.status === 'fulfilled') setPlans(plansRes.value);
      if (subRes.status === 'fulfilled') setSubscription(subRes.value);
      if (usageRes.status === 'fulfilled') setUsage(usageRes.value);
      if (invRes.status === 'fulfilled') setInvoices(invRes.value);
    } catch {
      // Fall back to demo data silently
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const currentPlan = subscription ? plans.find((p) => p.id === sub.plan) ?? plans[0] : null;

  // Self-hosted mode — no billing provider configured
  if (!loading && !subscription) {
    return (
      <div className="max-w-4xl mx-auto">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mt-0 mb-1">Billing</h1>
        <p className="text-sm text-muted-foreground mb-lg">Manage your subscription and usage.</p>
        <Card className="p-lg text-center">
          <Server className="mx-auto h-10 w-10 text-primary mb-md" />
          <h2 className="text-lg font-semibold mb-2">Self-Hosted Instance</h2>
          <p className="text-sm text-muted-foreground max-w-md mx-auto mb-md">
            You are running Sagewai on your own infrastructure. There is no billing
            provider configured. All features are available under the AGPL-3.0 license.
          </p>
          <p className="text-xs text-muted-foreground">
            For commercial licensing, contact{' '}
            <a href="mailto:licensing@sagewai.ai" className="text-primary no-underline hover:underline">licensing@sagewai.ai</a>
          </p>
        </Card>
      </div>
    );
  }

  // After the guard above, subscription + usage are guaranteed non-null
  const sub = subscription as BillingSubscription;
  const usg = usage as BillingUsage;

  const handleUpgrade = async (planId: string) => {
    try {
      const result = await adminApi.createCheckoutSession(planId);
      // In production this would redirect to Stripe checkout
      toast('success', `Checkout session created. Redirect URL: ${result.url}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create checkout session';
      toast('error', msg);
    }
  };

  const handleManageSubscription = async () => {
    try {
      const result = await adminApi.createBillingPortal();
      toast('info', `Billing portal: ${result.url}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to open billing portal';
      toast('error', msg);
    }
  };

  // Plan-based limits for usage meters
  const runLimit = typeof currentPlan?.features?.agent_runs_monthly === 'number'
    ? currentPlan.features.agent_runs_monthly as number
    : 10000;
  const storageLimit = typeof currentPlan?.features?.storage_gb === 'number'
    ? currentPlan.features.storage_gb as number
    : 50;
  const workerLimit = typeof currentPlan?.features?.workers === 'number'
    ? currentPlan.features.workers as number
    : 10;

  if (loading) {
    return (
      <div className="space-y-lg">
        <div>
          <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)]">Billing</h1>
          <p className="text-text-on-dark/50 text-sm mt-1">
            Manage your subscription, usage, and invoices
          </p>
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-md">
          <Skeleton className="h-48" />
          <Skeleton className="h-48 lg:col-span-2" />
        </div>
        <Skeleton className="h-64" />
        <Skeleton className="h-48" />
      </div>
    );
  }

  return (
    <div className="space-y-lg">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)]">Billing</h1>
        <p className="text-text-on-dark/50 text-sm mt-1">
          Manage your subscription, usage, and invoices
        </p>
      </div>

      {/* Current plan + usage meters */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-md">
        {/* Current plan card */}
        <Card className="p-md space-y-md">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs text-text-on-dark/40 uppercase tracking-wider font-semibold">
                Current Plan
              </p>
              <h2 className="text-xl font-bold mt-1">{currentPlan?.name ?? 'Free'}</h2>
            </div>
            <Badge variant={statusVariant(sub.status)}>
              {sub.status}
            </Badge>
          </div>

          <div className="text-3xl font-bold font-[family-name:var(--font-mono)]">
            {currentPlan?.price_monthly != null ? (
              <>
                ${currentPlan.price_monthly}
                <span className="text-sm font-normal text-text-on-dark/40"> /mo</span>
              </>
            ) : (
              <span className="text-lg">Custom Pricing</span>
            )}
          </div>

          {sub.cancel_at_period_end && (
            <p className="text-xs text-yellow-400">
              Cancels at end of period
            </p>
          )}

          <p className="text-xs text-text-on-dark/40">
            Current period ends{' '}
            {new Date(sub.current_period_end).toLocaleDateString('en-US', {
              month: 'long',
              day: 'numeric',
              year: 'numeric',
            })}
          </p>

          <Button
            variant="secondary"
            className="w-full"
            onClick={handleManageSubscription}
          >
            <CreditCard size={14} className="mr-2" />
            Manage Subscription
          </Button>
        </Card>

        {/* Usage meters */}
        <Card className="p-md space-y-md lg:col-span-2">
          <div className="flex items-center justify-between">
            <p className="text-xs text-text-on-dark/40 uppercase tracking-wider font-semibold">
              Current Period Usage
            </p>
            <span className="text-xs text-text-on-dark/30 font-mono">
              {usg.period_start} -- {usg.period_end}
            </span>
          </div>

          <div className="space-y-md">
            <UsageMeter
              label="Agent Runs"
              icon={Zap}
              used={usg.agent_runs}
              limit={runLimit}
            />
            <UsageMeter
              label="Storage"
              icon={HardDrive}
              used={usg.storage_used_gb}
              limit={storageLimit}
              unit="GB"
            />
            <UsageMeter
              label="Workers"
              icon={Server}
              used={usg.workers_active}
              limit={workerLimit}
            />
          </div>

          <div className="flex items-center gap-md text-xs text-text-muted pt-sm border-t border-border">
            <span className="flex items-center gap-1">
              <Activity size={12} />
              {usg.api_calls.toLocaleString()} API calls
            </span>
            <span>
              {usg.connectors_active} connectors active
            </span>
          </div>
        </Card>
      </div>

      {/* Plan comparison */}
      <Card className="p-md">
        <h3 className="text-sm font-semibold mb-md">Compare Plans</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="text-left py-3 pr-4 text-text-muted font-medium">Feature</th>
                {plans.map((plan) => (
                  <th key={plan.id} className="text-center py-3 px-4 font-medium">
                    <div className="flex flex-col items-center gap-1">
                      <span className={plan.id === sub.plan ? 'text-primary' : ''}>
                        {plan.name}
                      </span>
                      <span className="text-xs text-text-muted font-normal">
                        {plan.price_monthly != null
                          ? plan.price_monthly === 0
                            ? 'Free'
                            : `$${plan.price_monthly}/mo`
                          : 'Custom'}
                      </span>
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {['workers', 'connectors', 'agent_runs_monthly', 'storage_gb', 'fleet', 'premium_support'].map(
                (featureKey) => (
                  <tr key={featureKey} className="border-b border-border">
                    <td className="py-3 pr-4 text-text-secondary">{featureLabel(featureKey)}</td>
                    {plans.map((plan) => {
                      const val = plan.features[featureKey];
                      const isBool = typeof val === 'boolean';
                      return (
                        <td key={plan.id} className="text-center py-3 px-4">
                          {isBool ? (
                            val ? (
                              <Check size={16} className="inline text-green-400" />
                            ) : (
                              <span className="text-text-on-dark/20">--</span>
                            )
                          ) : (
                            <span className="font-mono text-xs">
                              {featureValue(featureKey, val as number)}
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ),
              )}
              {/* Action row */}
              <tr>
                <td className="py-4" />
                {plans.map((plan) => (
                  <td key={plan.id} className="text-center py-4 px-4">
                    {plan.id === sub.plan ? (
                      <Badge variant="info">Current</Badge>
                    ) : plan.id === 'enterprise' ? (
                      <a
                        href="mailto:sales@sagewai.ai"
                        className="inline-flex items-center gap-1 text-xs text-primary hover:text-primary-hover transition-colors"
                      >
                        Contact Sales
                        <ArrowRight size={12} />
                      </a>
                    ) : (
                      <Button
                        size="sm"
                        onClick={() => handleUpgrade(plan.id)}
                      >
                        {plan.price_monthly != null &&
                        currentPlan?.price_monthly != null &&
                        plan.price_monthly > currentPlan.price_monthly
                          ? 'Upgrade'
                          : 'Select'}
                        <ArrowRight size={12} className="ml-1" />
                      </Button>
                    )}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </Card>

      {/* Invoice history */}
      <Card className="p-md">
        <div className="flex items-center justify-between mb-md">
          <h3 className="text-sm font-semibold">Invoice History</h3>
          <Button variant="ghost" onClick={handleManageSubscription}>
            <ExternalLink size={12} className="mr-1" />
            Billing Portal
          </Button>
        </div>

        {invoices.length === 0 ? (
          <p className="text-sm text-text-on-dark/40 py-md text-center">No invoices yet</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-text-muted">
                <th className="text-left py-2 font-medium">Date</th>
                <th className="text-left py-2 font-medium">Invoice</th>
                <th className="text-right py-2 font-medium">Amount</th>
                <th className="text-center py-2 font-medium">Status</th>
                <th className="text-right py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {invoices.map((inv) => (
                <tr key={inv.id} className="border-b border-border">
                  <td className="py-3 text-text-on-dark/70">
                    {new Date(inv.date).toLocaleDateString('en-US', {
                      month: 'short',
                      day: 'numeric',
                      year: 'numeric',
                    })}
                  </td>
                  <td className="py-3 font-mono text-xs text-text-on-dark/50">{inv.id}</td>
                  <td className="py-3 text-right font-mono">${inv.amount.toFixed(2)}</td>
                  <td className="py-3 text-center">
                    <Badge variant={statusVariant(inv.status)}>{inv.status}</Badge>
                  </td>
                  <td className="py-3 text-right">
                    <a
                      href={inv.pdf_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-text-on-dark/50 hover:text-text-on-dark transition-colors"
                    >
                      <FileText size={12} />
                      PDF
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
