'use client';

import { useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { Project } from '@/utils/types';
import { ScopeBadge } from './scope-badge';

interface ScopeSelectorProps {
  /** Currently selected scope (org | project) */
  scope: string;
  /** Currently selected scope ID */
  scopeId: string;
  /** Called when scope or scope ID changes */
  onChange: (scope: string, scopeId: string) => void;
  /** Compact layout (single row) vs stacked */
  layout?: 'row' | 'stacked';
  /** Hide the org option */
  hideOrg?: boolean;
}

export function ScopeSelector({
  scope,
  scopeId,
  onChange,
  layout = 'stacked',
  hideOrg = false,
}: ScopeSelectorProps) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loadingProjects, setLoadingProjects] = useState(false);

  useEffect(() => {
    async function load() {
      setLoadingProjects(true);
      try {
        const projectsData = await adminApi.listProjects().catch(() => [] as Project[]);
        setProjects(projectsData);
      } finally {
        setLoadingProjects(false);
      }
    }
    load();
  }, []);

  function handleScopeChange(newScope: string) {
    // When switching scope, auto-select the first available ID or clear it
    if (newScope === 'org') {
      onChange(newScope, '');
    } else if (newScope === 'project') {
      onChange(newScope, projects.length > 0 ? projects[0].slug : '');
    } else {
      onChange(newScope, '');
    }
  }

  const scopes = hideOrg
    ? ['project'] as const
    : ['org', 'project'] as const;

  const isRow = layout === 'row';
  const containerClass = isRow ? 'flex items-end gap-3' : 'space-y-md';

  return (
    <div className={containerClass}>
      {/* Scope type selector */}
      <div className={isRow ? '' : ''}>
        <label className="text-xs text-text-muted block mb-1">Scope</label>
        <select
          value={scope}
          onChange={(e) => handleScopeChange(e.target.value)}
          className="w-full bg-bg-surface border border-border rounded px-2.5 py-2 text-sm focus:outline-none focus:border-primary"
        >
          {scopes.map((s) => (
            <option key={s} value={s}>
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>
      </div>

      {/* Scope ID selector — contextual per scope type */}
      {scope === 'org' ? (
        <div className={isRow ? 'flex-1' : ''}>
          <label className="text-xs text-text-muted block mb-1">&nbsp;</label>
          <div className="flex items-center gap-2 px-2.5 py-2 bg-bg-surface border border-border rounded text-sm text-text-muted">
            <ScopeBadge scope="org" />
            <span className="text-xs">Organization-wide — no ID required</span>
          </div>
        </div>
      ) : scope === 'project' ? (
        <div className={isRow ? 'flex-1' : ''}>
          <label className="text-xs text-text-muted block mb-1">Project</label>
          {loadingProjects ? (
            <div className="h-9 bg-bg-subtle rounded animate-pulse" />
          ) : projects.length === 0 ? (
            <div className="flex items-center gap-2 px-2.5 py-2 bg-bg-surface border border-warning/30 rounded text-xs text-warning">
              No projects found. Create a project in Settings → Applications first.
            </div>
          ) : (
            <select
              value={scopeId}
              onChange={(e) => onChange(scope, e.target.value)}
              className="w-full bg-bg-surface border border-border rounded px-2.5 py-2 text-sm focus:outline-none focus:border-primary"
            >
              {projects.map((p) => (
                <option key={p.slug} value={p.slug}>
                  {p.name} ({p.slug})
                </option>
              ))}
            </select>
          )}
        </div>
      ) : null}
    </div>
  );
}
