'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { ChevronDown, ChevronRight } from 'lucide-react';
import type { AutopilotMission } from '@/utils/types';
import { AutopilotStatusBadge } from '@/components/autopilot-status-badge';

const MODE_LABELS: Record<string, string> = {
  scheduled: 'Scheduled',
  event_driven: 'Event-driven',
  batch: 'Batch',
};

function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt) return '—';
  const start = new Date(startedAt).getTime();
  const end = finishedAt ? new Date(finishedAt).getTime() : Date.now();
  const diffMs = end - start;
  const secs = Math.floor(diffMs / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function MissionRow({
  mission,
  liveStatus,
  onCancel,
}: {
  mission: AutopilotMission;
  liveStatus?: string;
  onCancel?: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const status = liveStatus ?? mission.status;
  const router = useRouter();

  return (
    <>
      <tr
        className="border-b border-border hover:bg-bg-subtle transition-colors cursor-pointer"
        data-testid={`mission-row-${mission.id}`}
        onClick={() => router.push(`/autopilot/missions/${encodeURIComponent(mission.id)}`)}
      >
        <td className="py-3 px-4">
          <button
            type="button"
            data-testid={`mission-row-chevron-${mission.id}`}
            aria-label={expanded ? 'Collapse row details' : 'Expand row details'}
            aria-expanded={expanded}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            className="flex items-center gap-2 cursor-pointer rounded hover:bg-bg-subtle/60 p-1 -m-1"
          >
            {expanded ? (
              <ChevronDown size={13} className="text-text-muted shrink-0" />
            ) : (
              <ChevronRight size={13} className="text-text-muted shrink-0" />
            )}
            <span className="font-[family-name:var(--font-mono)] text-xs text-text-muted">
              {mission.id.slice(0, 8)}
            </span>
          </button>
        </td>
        <td className="py-3 px-4">
          <span className="text-sm font-medium text-text-primary">{mission.blueprint_title}</span>
          <span className="ml-2 text-[10px] text-text-muted">{mission.blueprint_category}</span>
        </td>
        <td className="py-3 px-4">
          <AutopilotStatusBadge status={status} />
        </td>
        <td className="py-3 px-4 hidden sm:table-cell">
          <span className="text-[11px] bg-bg-subtle px-2 py-0.5 rounded text-text-secondary">
            {MODE_LABELS[mission.mode] ?? mission.mode}
          </span>
        </td>
        <td className="py-3 px-4 hidden md:table-cell text-sm text-text-secondary">
          {formatDate(mission.started_at)}
        </td>
        <td className="py-3 px-4 hidden md:table-cell text-sm text-text-secondary">
          {formatDuration(mission.started_at, mission.finished_at)}
        </td>
        <td className="py-3 px-4 hidden lg:table-cell text-sm text-text-secondary">
          {mission.project_id ?? <span className="text-text-muted italic">global</span>}
        </td>
        {onCancel && (
          <td className="py-3 px-4" onClick={(e) => e.stopPropagation()}>
            {status === 'running' && (
              <button
                type="button"
                onClick={() => onCancel(mission.id)}
                className="text-xs px-2 py-1 rounded border border-error/40 text-error hover:bg-error/10 transition-colors cursor-pointer"
              >
                Cancel
              </button>
            )}
          </td>
        )}
      </tr>
      {expanded && (
        <tr className="border-b border-border bg-bg-subtle/50">
          <td colSpan={onCancel ? 8 : 7} className="px-4 py-3">
            {mission.steps.length === 0 ? (
              <p className="text-xs text-text-muted italic m-0">No step results yet.</p>
            ) : (
              <ul className="m-0 p-0 list-none space-y-2">
                {mission.steps.map((step, i) => (
                  <li key={i} className="flex items-start gap-3">
                    <AutopilotStatusBadge status={step.status} />
                    <div>
                      <span className="text-xs font-medium text-text-primary">{step.step}</span>
                      {step.output && (
                        <p className="text-xs text-text-secondary m-0 mt-0.5 font-[family-name:var(--font-mono)] whitespace-pre-wrap">
                          {step.output}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

interface AutopilotMissionListProps {
  missions: AutopilotMission[];
  /** Live status overrides from SSE — maps mission_id → current status */
  liveStatuses?: Record<string, string>;
  onCancel?: (id: string) => void;
}

export function AutopilotMissionList({
  missions,
  liveStatuses,
  onCancel,
}: AutopilotMissionListProps) {
  if (missions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-center">
        <p className="text-sm font-medium text-text-primary m-0 mb-1">No missions yet</p>
        <p className="text-sm text-text-secondary m-0">
          Submit a goal above to create your first autopilot mission.
        </p>
      </div>
    );
  }

  return (
    <div className="bg-bg-surface rounded-lg border border-border overflow-hidden">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-border">
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide w-24">
              ID
            </th>
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide">
              Blueprint
            </th>
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide">
              Status
            </th>
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide hidden sm:table-cell">
              Mode
            </th>
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide hidden md:table-cell">
              Started
            </th>
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide hidden md:table-cell">
              Duration
            </th>
            <th className="text-left py-3 px-4 text-xs font-semibold text-text-muted uppercase tracking-wide hidden lg:table-cell">
              Project
            </th>
            {onCancel && <th className="py-3 px-4 w-20" />}
          </tr>
        </thead>
        <tbody>
          {missions.map((mission) => (
            <MissionRow
              key={mission.id}
              mission={mission}
              liveStatus={liveStatuses?.[mission.id]}
              onCancel={onCancel}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
