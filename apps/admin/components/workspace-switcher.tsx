'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { ChevronDown, Check, Globe, FolderOpen } from 'lucide-react';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { Workspace } from '@/utils/types';

export function WorkspaceSwitcher() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [currentWs, setCurrentWs] = useState<Workspace | null>(null);
  const [projectOpen, setProjectOpen] = useState(false);
  const { projects, current: currentProject, currentSlug, setProject, isGlobal } = useProject();
  const ref = useRef<HTMLDivElement>(null);

  const fetchWorkspaces = useCallback(async () => {
    try {
      const data = await adminApi.listWorkspaces();
      setWorkspaces(data);
      if (data.length > 0 && !currentWs) setCurrentWs(data[0]);
    } catch { /* ignore */ }
  }, [currentWs]);

  useEffect(() => { fetchWorkspaces(); }, [fetchWorkspaces]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setProjectOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div ref={ref} className="mb-4 space-y-1.5">
      {/* Org name */}
      <div className="px-3 py-1.5 text-[11px] font-semibold text-text-muted uppercase tracking-wider">
        {currentWs?.name ?? 'Sagewai'}
      </div>

      {/* Project selector */}
      <div className="relative">
        <button
          onClick={() => setProjectOpen(!projectOpen)}
          className="w-full px-3 py-2 bg-primary/5 border border-primary/15 rounded-lg cursor-pointer text-left text-[13px] flex justify-between items-center hover:bg-primary/10 transition-colors"
        >
          <span className="flex items-center gap-2 font-medium text-text-primary truncate">
            {isGlobal ? (
              <>
                <Globe size={14} className="text-text-muted flex-shrink-0" />
                All Projects
              </>
            ) : (
              <>
                <FolderOpen size={14} className="text-primary flex-shrink-0" />
                {currentProject?.name ?? currentSlug}
              </>
            )}
          </span>
          <ChevronDown size={14} className={`text-text-muted transition-transform ${projectOpen ? 'rotate-180' : ''}`} />
        </button>

        {projectOpen && (
          <div className="absolute top-full left-0 right-0 mt-1 bg-bg-elevated border border-border rounded-lg shadow-lg z-50 overflow-hidden">
            {/* Global option */}
            <button
              onClick={() => { setProject(null); setProjectOpen(false); }}
              className={`w-full px-3 py-2 border-none cursor-pointer text-left text-[13px] flex items-center gap-2 ${
                isGlobal ? 'bg-primary/5 text-primary' : 'bg-transparent text-text-primary hover:bg-bg-subtle'
              }`}
            >
              <Globe size={14} />
              <span className="flex-1">All Projects</span>
              {isGlobal && <Check size={14} />}
            </button>

            {projects.length > 0 && (
              <div className="border-t border-border" />
            )}

            {/* Project list */}
            {projects.map((p) => (
              <button
                key={p.slug}
                onClick={() => { setProject(p.slug); setProjectOpen(false); }}
                className={`w-full px-3 py-2 border-none cursor-pointer text-left text-[13px] flex items-center gap-2 ${
                  p.slug === currentSlug ? 'bg-primary/5 text-primary' : 'bg-transparent text-text-primary hover:bg-bg-subtle'
                }`}
              >
                <FolderOpen size={14} />
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate">{p.name}</div>
                  <div className="text-[11px] text-text-muted">{p.environment}</div>
                </div>
                {p.slug === currentSlug && <Check size={14} />}
              </button>
            ))}

            <div className="border-t border-border" />
            <a
              href="/settings/projects"
              className="block px-3 py-2 text-center text-[13px] text-primary no-underline hover:bg-bg-subtle transition-colors"
            >
              Manage Projects
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
