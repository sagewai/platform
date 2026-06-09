'use client';

/**
 * Project context — provides the currently selected project to all
 * admin pages. Every API call includes the project_id via the
 * X-Project-ID header.
 *
 * Scope model:
 *   project_id = null   → Org-global (visible everywhere)
 *   project_id = "id"   → Project-scoped (strict isolation)
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import type { Project } from './types';
import { adminApi } from './api';

const LS_KEY = 'sagewai-project';

interface ProjectContextValue {
  /** All projects in the org. */
  projects: Project[];
  /** Currently selected project (null = org-global view). */
  current: Project | null;
  /** Current project slug (null = global). */
  currentSlug: string | null;
  /** Switch to a project (null = global). */
  setProject: (slug: string | null) => void;
  /** True when viewing org-global (all projects). */
  isGlobal: boolean;
  /** Refresh the project list (e.g., after creating a new project). */
  refresh: () => void;
}

const ProjectContext = createContext<ProjectContextValue>({
  projects: [],
  current: null,
  currentSlug: null,
  setProject: () => {},
  isGlobal: true,
  refresh: () => {},
});

import { setCurrentProjectId } from './project-state';
// Re-export for convenience — components can import from either module
export { getCurrentProjectId } from './project-state';

export function ProjectProvider({ children }: { children: ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentSlug, setCurrentSlug] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const selectProject = useCallback((project: Project | null) => {
    setCurrentSlug(project?.slug ?? null);
    setCurrentProjectId(project?.id ?? null);
    if (project) {
      localStorage.setItem(LS_KEY, project.slug);
    } else {
      localStorage.removeItem(LS_KEY);
    }
  }, []);

  const fetchProjects = useCallback(async () => {
    try {
      const ps = await adminApi.listProjects();
      setProjects(ps);

      if (!loaded) {
        // First load — restore from localStorage or pick default
        const saved = localStorage.getItem(LS_KEY);
        const savedProject = saved ? ps.find((p) => p.slug === saved) ?? null : null;
        if (savedProject) {
          selectProject(savedProject);
        } else if (ps.length > 0) {
          // Auto-select default (first) project
          selectProject(ps[0]);
        }
        setLoaded(true);
      }
    } catch {
      // Backend not ready or setup not complete — no projects yet
    }
  }, [loaded, selectProject]);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  const setProject = useCallback((slug: string | null) => {
    if (slug === null) {
      selectProject(null);
      return;
    }
    selectProject(projects.find((p) => p.slug === slug) ?? null);
  }, [projects, selectProject]);

  const current = currentSlug ? projects.find((p) => p.slug === currentSlug) ?? null : null;

  return (
    <ProjectContext.Provider
      value={{
        projects,
        current,
        currentSlug,
        setProject,
        isGlobal: currentSlug === null,
        refresh: fetchProjects,
      }}
    >
      {children}
    </ProjectContext.Provider>
  );
}

export function useProject() {
  return useContext(ProjectContext);
}
