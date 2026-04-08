'use client';

import { useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { OrgSettings } from '@/utils/types';
import { Card, Button, FormField, TextInput, Select, Skeleton, useToast } from '@sagecurator/ui';

const TIMEZONE_OPTIONS = [
  { value: 'UTC', label: 'UTC' },
  { value: 'America/New_York', label: 'Eastern Time (US)' },
  { value: 'America/Chicago', label: 'Central Time (US)' },
  { value: 'America/Denver', label: 'Mountain Time (US)' },
  { value: 'America/Los_Angeles', label: 'Pacific Time (US)' },
  { value: 'Europe/London', label: 'London' },
  { value: 'Europe/Berlin', label: 'Berlin / Paris' },
  { value: 'Europe/Istanbul', label: 'Istanbul' },
  { value: 'Asia/Dubai', label: 'Dubai' },
  { value: 'Asia/Tokyo', label: 'Tokyo' },
  { value: 'Asia/Singapore', label: 'Singapore' },
  { value: 'Australia/Sydney', label: 'Sydney' },
];

export default function OrganizationSettingsPage() {
  const [org, setOrg] = useState<OrgSettings | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  const [orgName, setOrgName] = useState('');
  const [appUrl, setAppUrl] = useState('');
  const [contactEmail, setContactEmail] = useState('');
  const [timezone, setTimezone] = useState('UTC');
  const [industry, setIndustry] = useState('');
  const [teamSize, setTeamSize] = useState('');

  useEffect(() => {
    adminApi.getOrganization()
      .then((data) => {
        setOrg(data);
        setOrgName(data.org_name);
        setAppUrl(data.app_url);
        setContactEmail(data.contact_email);
        setTimezone(data.timezone || 'UTC');
        setIndustry(data.industry);
        setTeamSize(data.team_size);
      })
      .catch(() => setError('Failed to load organization settings.'))
      .finally(() => setLoading(false));
  }, []);

  async function handleSave() {
    if (!orgName.trim()) return;
    setSaving(true);
    setError(null);
    try {
      const updated = await adminApi.updateOrganization({
        org_name: orgName,
        app_url: appUrl,
        contact_email: contactEmail,
        timezone,
        industry,
        team_size: teamSize,
      });
      setOrg(updated);
      toast('success', 'Organization settings saved');
    } catch {
      setError('Failed to save organization settings.');
    } finally {
      setSaving(false);
    }
  }

  function formatDate(iso: string): string {
    if (!iso) return '—';
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
    });
  }

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-lg">
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Organization Settings
        </h1>
        <p className="m-0 text-sm text-text-secondary">
          Edit your organization details. These settings were collected during first-run setup.
        </p>
      </div>

      {error && (
        <div
          className="bg-error-light border border-error/20 rounded-lg px-4 py-3 text-error text-sm mb-md"
          role="alert"
        >
          {error}
        </div>
      )}

      {loading ? (
        <Card>
          <Skeleton lines={6} />
        </Card>
      ) : (
        <Card>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-md mb-md">
            <FormField label="Organization Name" required>
              <TextInput
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                placeholder="Acme Corp"
              />
            </FormField>

            <FormField label="App URL" hint="Public URL of your Sagewai deployment">
              <TextInput
                value={appUrl}
                onChange={(e) => setAppUrl(e.target.value)}
                placeholder="https://ai.acme.com"
                type="url"
              />
            </FormField>

            <FormField label="Contact Email" hint="Admin contact for system notifications">
              <TextInput
                value={contactEmail}
                onChange={(e) => setContactEmail(e.target.value)}
                placeholder="admin@acme.com"
                type="email"
              />
            </FormField>

            <FormField label="Timezone">
              <Select value={timezone} onChange={(e) => setTimezone(e.target.value)}>
                {TIMEZONE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </FormField>

            <FormField label="Industry">
              <TextInput
                value={industry}
                onChange={(e) => setIndustry(e.target.value)}
                placeholder="e.g. Technology, Finance, Healthcare"
              />
            </FormField>

            <FormField label="Team Size">
              <TextInput
                value={teamSize}
                onChange={(e) => setTeamSize(e.target.value)}
                placeholder="e.g. 1–10, 11–50, 51–200"
              />
            </FormField>
          </div>

          <Button onClick={handleSave} disabled={saving || !orgName.trim()}>
            {saving ? 'Saving…' : 'Save Changes'}
          </Button>

          {/* Read-only fields */}
          {org && (
            <div className="mt-lg pt-lg border-t border-border grid grid-cols-1 md:grid-cols-3 gap-md">
              <div>
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-1">
                  Org Slug
                </p>
                <p className="text-sm font-[family-name:var(--font-mono)] text-text-secondary m-0">
                  {org.org_slug}
                </p>
              </div>
              <div>
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-1">
                  Admin Email
                </p>
                <p className="text-sm text-text-secondary m-0">{org.admin_email}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-1">
                  Setup Date
                </p>
                <p className="text-sm text-text-secondary m-0">{formatDate(org.completed_at)}</p>
              </div>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
