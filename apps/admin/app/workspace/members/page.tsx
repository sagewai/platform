'use client';

import { useEffect, useState, useCallback } from 'react';
import { adminApi } from '@/utils/api';
import type { WorkspaceMember, Workspace } from '@/utils/types';
import { Card, Button, Badge, EmptyState, Skeleton, useToast } from '@/components/ui/legacy';

const ROLE_VARIANTS: Record<string, 'default' | 'success' | 'info' | 'warning'> = {
  owner: 'info',
  admin: 'success',
  member: 'default',
  viewer: 'default',
};

export default function MembersPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [wsId, setWsId] = useState('');
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [loading, setLoading] = useState(true);
  const { toast } = useToast();

  // Invite form
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('member');
  const [inviting, setInviting] = useState(false);

  const fetchWorkspaces = useCallback(async () => {
    try {
      const data = await adminApi.listWorkspaces();
      setWorkspaces(data);
      if (data.length > 0 && !wsId) {
        setWsId(data[0].id);
      }
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [wsId]);

  const fetchMembers = useCallback(async () => {
    if (!wsId) return;
    try {
      const data = await adminApi.listMembers(wsId);
      setMembers(data);
    } catch { /* ignore */ }
  }, [wsId]);

  useEffect(() => { fetchWorkspaces(); }, [fetchWorkspaces]);
  useEffect(() => { fetchMembers(); }, [fetchMembers]);

  async function handleInvite() {
    if (!inviteEmail || !wsId) return;
    setInviting(true);
    try {
      await adminApi.inviteMember(wsId, inviteEmail, inviteRole);
      toast('success', `Invitation sent to ${inviteEmail}`);
      setInviteEmail('');
      fetchMembers();
    } catch {
      toast('error', 'Failed to send invitation.');
    } finally {
      setInviting(false);
    }
  }

  async function handleRoleChange(userId: string, newRole: string) {
    if (!wsId) return;
    try {
      await adminApi.updateMemberRole(wsId, userId, newRole);
      fetchMembers();
    } catch { /* ignore */ }
  }

  async function handleRemove(userId: string) {
    if (!wsId || !confirm('Remove this member?')) return;
    try {
      await adminApi.removeMember(wsId, userId);
      toast('success', 'Member removed');
      fetchMembers();
    } catch { /* ignore */ }
  }

  if (loading) return <Skeleton lines={5} />;

  return (
    <div className="max-w-[800px] mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Workspace Members</h1>

      {/* Invite form */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-[15px] font-semibold font-[family-name:var(--font-heading)]">Invite Member</h3>
        <div className="flex gap-3 items-end">
          <div className="flex-1">
            <span className="text-xs font-medium text-text-secondary block mb-1">Email</span>
            <input value={inviteEmail} onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="colleague@company.com" className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface box-border" />
          </div>
          <div className="w-[120px]">
            <span className="text-xs font-medium text-text-secondary block mb-1">Role</span>
            <select value={inviteRole} onChange={(e) => setInviteRole(e.target.value)}
              className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface">
              <option value="admin">Admin</option>
              <option value="member">Member</option>
              <option value="viewer">Viewer</option>
            </select>
          </div>
          <Button onClick={handleInvite} disabled={inviting}>
            {inviting ? 'Sending...' : 'Send Invite'}
          </Button>
        </div>
      </Card>

      {/* Member list */}
      <Card>
        <h3 className="mt-0 mb-md text-[15px] font-semibold font-[family-name:var(--font-heading)]">
          Members ({members.length})
        </h3>
        {members.length === 0 ? (
          <EmptyState title="No Members" description="No members yet." />
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border">
                <th className="text-left py-2 text-xs text-text-muted uppercase tracking-wide">Member</th>
                <th className="text-left py-2 text-xs text-text-muted uppercase tracking-wide">Role</th>
                <th className="text-right py-2 text-xs text-text-muted uppercase tracking-wide">Actions</th>
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.user_id} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
                  <td className="py-3">
                    <div className="font-medium">{m.display_name || m.email}</div>
                    <div className="text-xs text-text-muted">{m.email}</div>
                  </td>
                  <td className="py-3">
                    {m.role === 'owner' ? (
                      <Badge variant={ROLE_VARIANTS.owner}>owner</Badge>
                    ) : (
                      <select value={m.role} onChange={(e) => handleRoleChange(m.user_id, e.target.value)}
                        className="px-2 py-1 border border-border rounded text-xs bg-bg-surface">
                        <option value="admin">admin</option>
                        <option value="member">member</option>
                        <option value="viewer">viewer</option>
                      </select>
                    )}
                  </td>
                  <td className="py-3 text-right">
                    {m.role !== 'owner' && (
                      <Button variant="secondary" className="text-error border-error text-xs" onClick={() => handleRemove(m.user_id)}>
                        Remove
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
