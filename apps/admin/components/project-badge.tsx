'use client';

import { useProject } from '@/utils/project-context';
import { FolderOpen, Globe } from 'lucide-react';

/**
 * Shows the currently selected project scope as a badge.
 * Use in page headers: `<h1>Dashboard <ProjectBadge /></h1>`
 */
export function ProjectBadge() {
  const { current, isGlobal } = useProject();

  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium bg-primary/10 text-primary ml-2">
      {isGlobal ? (
        <>
          <Globe size={12} />
          All Projects
        </>
      ) : (
        <>
          <FolderOpen size={12} />
          {current?.name ?? 'Unknown'}
        </>
      )}
    </span>
  );
}
