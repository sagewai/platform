/**
 * Module-level project state — shared between client and server contexts.
 *
 * This file has NO 'use client' directive so it can be safely imported
 * by api.ts (used in server components) and project-context.tsx (client).
 *
 * The ProjectProvider sets the value; the API client reads it.
 */

let _currentProjectId: string | null = null;

export function getCurrentProjectId(): string | null {
  return _currentProjectId;
}

export function setCurrentProjectId(id: string | null): void {
  _currentProjectId = id;
}
