'use client';

import type { AutopilotMissionDetail } from '@/utils/types';

function PanelCard({
  title,
  testId,
  children,
}: {
  title: string;
  testId?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className="rounded-lg border border-border bg-bg-surface p-4"
      data-testid={testId}
    >
      <h3 className="text-sm font-semibold text-text-primary mb-2">{title}</h3>
      {children}
    </section>
  );
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return <p className="text-sm text-text-secondary m-0">{children}</p>;
}

function stringifySlotValue(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
    return String(v);
  }
  try {
    return JSON.stringify(v);
  } catch {
    return '<unserialisable>';
  }
}

export function AutopilotResourcePanels({ mission }: { mission: AutopilotMissionDetail }) {
  const slotEntries = Object.entries(mission.slots ?? {}).filter(([k]) => !k.startsWith('__'));
  const toolsRequired = mission.tools_required ?? [];
  const providersRequired = mission.providers_required ?? [];
  const successCriteria = mission.success_criteria ?? [];
  const trainingHooks = mission.training_data_hooks ?? [];

  return (
    <>
      <PanelCard title="Slots" testId="resource-panel-slots">
        {slotEntries.length === 0 ? (
          <EmptyHint>No slots declared.</EmptyHint>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {slotEntries.map(([k, v]) => (
                <tr key={k}>
                  <td className="pr-3 py-0.5 align-top font-[family-name:var(--font-mono)] text-text-secondary whitespace-nowrap">
                    {k}
                  </td>
                  <td className="py-0.5 font-[family-name:var(--font-mono)] text-text-primary break-all">
                    {stringifySlotValue(v)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </PanelCard>

      <PanelCard title="Tools required" testId="resource-panel-tools">
        {toolsRequired.length === 0 ? (
          <EmptyHint>None.</EmptyHint>
        ) : (
          <div className="flex flex-wrap gap-2">
            {toolsRequired.map((t) => (
              <span
                key={t.name}
                className="rounded-full border border-border bg-bg-subtle text-text-primary text-xs px-2 py-1 font-[family-name:var(--font-mono)]"
                title={t.description ?? undefined}
              >
                {t.name}
              </span>
            ))}
          </div>
        )}
      </PanelCard>

      <PanelCard title="Providers required" testId="resource-panel-providers">
        {providersRequired.length === 0 ? (
          <EmptyHint>None.</EmptyHint>
        ) : (
          <div className="flex flex-wrap gap-2">
            {providersRequired.map((p) => (
              <span
                key={`${p.name}-${p.role ?? ''}`}
                className="inline-flex items-center gap-1 rounded-full border border-border text-text-primary text-xs px-2 py-1"
              >
                <span className="font-[family-name:var(--font-mono)]">{p.name}</span>
                {p.tier && (
                  <span className="rounded bg-primary/10 text-primary text-[10px] px-1.5 py-0.5 uppercase tracking-wide">
                    {p.tier}
                  </span>
                )}
              </span>
            ))}
          </div>
        )}
      </PanelCard>

      <PanelCard title="Success criteria" testId="resource-panel-success-criteria">
        {successCriteria.length === 0 ? (
          <EmptyHint>None declared.</EmptyHint>
        ) : (
          <ul className="text-sm space-y-1 m-0 list-none p-0">
            {successCriteria.map((c, i) => (
              <li key={`${c.metric}-${i}`} className="flex items-baseline gap-2">
                <span className="font-[family-name:var(--font-mono)] text-text-secondary">
                  {c.metric}
                </span>
                <span className="text-text-secondary">{c.op ?? '≥'}</span>
                <span className="font-[family-name:var(--font-mono)] text-text-primary">
                  {String(c.target)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </PanelCard>

      <PanelCard title="Training data hooks" testId="resource-panel-training-hooks">
        {trainingHooks.length === 0 ? (
          <EmptyHint>No training hooks.</EmptyHint>
        ) : (
          <ul className="text-sm space-y-1 m-0 list-none p-0">
            {trainingHooks.map((h, i) => (
              <li key={`${h.event}-${i}`} className="flex items-baseline gap-2">
                <span className="font-[family-name:var(--font-mono)] text-text-secondary">
                  {h.event}
                </span>
                <span className="text-text-secondary">→</span>
                <span className="font-[family-name:var(--font-mono)] text-text-primary">
                  {h.dataset}
                </span>
                {h.format && (
                  <span className="rounded bg-bg-subtle text-text-secondary text-[10px] px-1.5 py-0.5">
                    {h.format}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </PanelCard>
    </>
  );
}
