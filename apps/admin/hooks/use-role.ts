'use client';

import { useMemo } from 'react';
import { getUserRole } from '@/utils/auth';
import {
  ROLE_PERMISSIONS,
  ROLE_NAV_GROUPS,
  ROLE_FAVORITES,
  ROLE_LABELS,
  type UserRole,
  type RolePermissions,
} from '@/utils/roles';

export interface UseRoleResult {
  role: UserRole;
  label: string;
  permissions: RolePermissions;
  navGroups: string[];
  favorites: { href: string; label: string }[];
}

/**
 * Hook to get the current user's role, permissions, and nav configuration.
 * Re-derives on each render (role changes require a page refresh since
 * the JWT is stored in-memory).
 */
export function useRole(): UseRoleResult {
  return useMemo(() => {
    const role = getUserRole();
    return {
      role,
      label: ROLE_LABELS[role],
      permissions: ROLE_PERMISSIONS[role],
      navGroups: ROLE_NAV_GROUPS[role],
      favorites: ROLE_FAVORITES[role],
    };
  }, []);
}
