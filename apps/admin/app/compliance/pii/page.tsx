'use client';

import { useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import { StatCard } from '@/components/stat-card';
import { PIIEventsChart } from '@/components/pii-events-chart';
import { PIIEntityBreakdown } from '@/components/pii-entity-breakdown';
import { ExportButton } from '@/components/export-button';
import { Badge, Card } from '@/components/ui/legacy';

/** Known PII entity types from the SDK PIIGuard. */
const PII_ENTITY_TYPES = [
  'EMAIL',
  'PHONE',
  'SSN',
  'CREDIT_CARD',
  'IP_ADDRESS',
  'ADDRESS',
  'NAME',
  'DATE_OF_BIRTH',
  'PASSPORT',
  'DRIVER_LICENSE',
];

export default function PIICompliancePage() {
  const [piiEvents, setPiiEvents] = useState(0);
  const [hallucinationFlags, setHallucinationFlags] = useState(0);
  const [contentFilterEvents, setContentFilterEvents] = useState(0);
  const [totalEvents, setTotalEvents] = useState(0);

  useEffect(() => {
    adminApi
      .getRisks()
      .then((risks) => {
        setPiiEvents(risks.pii_events);
        setHallucinationFlags(risks.hallucination_flags);
        setContentFilterEvents(risks.content_filter_events);
        setTotalEvents(risks.total_events);
      })
      .catch(() => {});
  }, []);

  // Build timeline data (single day for MVP in-memory store)
  const today = new Date().toISOString().slice(0, 10);
  const timelineData = totalEvents > 0
    ? [
        {
          date: today,
          pii: piiEvents,
          hallucination: hallucinationFlags,
          content_filter: contentFilterEvents,
        },
      ]
    : [];

  // Build entity breakdown (simulated distribution for MVP — real data
  // would come from detailed PII event records)
  const entityData = piiEvents > 0
    ? PII_ENTITY_TYPES.slice(0, 5).map((entity, idx) => ({
        entity,
        count: Math.max(1, Math.round(piiEvents * (1 / (idx + 1)) * 0.5)),
      }))
    : [];

  // Redaction actions
  const redacted = piiEvents;
  const flagged = hallucinationFlags;
  const blocked = contentFilterEvents;

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">PII Compliance Dashboard</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Monitor PII detection, hallucination flags, and content filter events across all agents.
      </p>

      {/* Summary cards */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-md mb-lg">
        <StatCard label="PII Detections" value={piiEvents} />
        <StatCard label="Hallucination Flags" value={hallucinationFlags} />
        <StatCard label="Content Filtered" value={contentFilterEvents} />
        <StatCard label="Total Events" value={totalEvents} />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-[repeat(auto-fit,minmax(400px,1fr))] gap-md mb-lg">
        <PIIEventsChart data={timelineData} />
        <PIIEntityBreakdown data={entityData} />
      </div>

      {/* Redaction actions summary */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Redaction Actions</h3>
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b-2 border-border">
              <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Action</th>
              <th className="text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Count</th>
              <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Status</th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-border">
              <td className="py-2.5 px-3">PII Redacted</td>
              <td className="py-2.5 px-3 text-right">{redacted}</td>
              <td className="py-2.5 px-3">
                <Badge variant="success">Protected</Badge>
              </td>
            </tr>
            <tr className="border-b border-border">
              <td className="py-2.5 px-3">Hallucination Flagged</td>
              <td className="py-2.5 px-3 text-right">{flagged}</td>
              <td className="py-2.5 px-3">
                <Badge variant="warning">Warned</Badge>
              </td>
            </tr>
            <tr className="border-b border-border last:border-0">
              <td className="py-2.5 px-3">Content Blocked</td>
              <td className="py-2.5 px-3 text-right">{blocked}</td>
              <td className="py-2.5 px-3">
                <Badge variant="error">Blocked</Badge>
              </td>
            </tr>
          </tbody>
        </table>
      </Card>

      {/* Export button */}
      <Card className="flex justify-between items-center">
        <div>
          <strong className="text-sm">Audit Report</strong>
          <p className="mt-1 mb-0 text-[13px] text-text-muted">
            Export a full PII compliance audit report for the current period.
          </p>
        </div>
        <div className="flex gap-2">
          <ExportButton format="json" label="Export JSON" params={{ event_type: 'pii_detected' }} />
          <ExportButton format="csv" label="Export CSV" params={{ event_type: 'pii_detected' }} />
        </div>
      </Card>
    </div>
  );
}
