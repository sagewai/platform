/** Role system — defines user roles, permissions, and role-based nav groups. */

export type UserRole = 'admin' | 'developer' | 'ml_engineer' | 'viewer';

export interface RolePermissions {
  canManageSystem: boolean;
  canBuild: boolean;
  canTrain: boolean;
  canView: boolean;
}

export const ROLE_LABELS: Record<UserRole, string> = {
  admin: 'Admin',
  developer: 'Developer',
  ml_engineer: 'ML Engineer',
  viewer: 'Viewer',
};

export const ROLE_PERMISSIONS: Record<UserRole, RolePermissions> = {
  admin: { canManageSystem: true, canBuild: true, canTrain: true, canView: true },
  developer: { canManageSystem: false, canBuild: true, canTrain: false, canView: true },
  ml_engineer: { canManageSystem: false, canBuild: false, canTrain: true, canView: true },
  viewer: { canManageSystem: false, canBuild: false, canTrain: false, canView: true },
};

/**
 * Nav group IDs visible to each role.
 * Groups not in this list are hidden from the sidebar for that role.
 */
export const ROLE_NAV_GROUPS: Record<UserRole, string[]> = {
  admin: [
    'home', 'build', 'intelligence', 'operations', 'observe',
    'harness', 'system', 'account',
  ],
  developer: [
    'home', 'build', 'intelligence', 'tools', 'debug', 'account',
  ],
  ml_engineer: [
    'home', 'training', 'data', 'analytics', 'intelligence', 'account',
  ],
  viewer: [
    'home', 'reports', 'agents-readonly', 'account',
  ],
};

/** Role-specific favorites for the sidebar quick-access strip. */
export const ROLE_FAVORITES: Record<UserRole, { href: string; label: string }[]> = {
  admin: [
    { href: '/', label: 'Dashboard' },
    { href: '/agents', label: 'Agents' },
    { href: '/tools/playground', label: 'Playground' },
    { href: '/system/organization', label: 'System' },
  ],
  developer: [
    { href: '/', label: 'Dashboard' },
    { href: '/agents', label: 'Agents' },
    { href: '/tools/playground', label: 'Playground' },
    { href: '/workflows', label: 'Workflows' },
  ],
  ml_engineer: [
    { href: '/', label: 'Dashboard' },
    { href: '/training/logs', label: 'Run Logs' },
    { href: '/training/evals', label: 'Evals' },
    { href: '/training/finetune', label: 'Fine-Tuning' },
  ],
  viewer: [
    { href: '/', label: 'Dashboard' },
    { href: '/analytics/costs', label: 'Costs' },
    { href: '/agents', label: 'Agents' },
  ],
};

const VALID_ROLES: UserRole[] = ['admin', 'developer', 'ml_engineer', 'viewer'];

/**
 * Extract the user role from a JWT access token payload.
 * Falls back to 'admin' for self-hosted single-user setups.
 */
export function parseRoleFromToken(token: string | null): UserRole {
  if (!token) return 'admin';
  try {
    const payload = JSON.parse(atob(token.split('.')[1]));
    const role = payload.role;
    if (role && VALID_ROLES.includes(role)) return role;
  } catch {
    // Malformed token — fallback
  }
  return 'admin';
}
