'use client';

import { useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { AccountInfo } from '@/utils/types';
import { Card, Button, FormField, TextInput, Skeleton, useToast } from '@/components/ui/legacy';

export default function AccountSettingsPage() {
  const [account, setAccount] = useState<AccountInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  // Profile
  const [displayName, setDisplayName] = useState('');
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState<string | null>(null);

  // Password
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  useEffect(() => {
    adminApi.getAccount()
      .then((data) => {
        setAccount(data);
        setDisplayName(data.display_name);
      })
      .catch(() => setError('Failed to load account information.'))
      .finally(() => setLoading(false));
  }, []);

  async function handleSaveProfile() {
    setSavingProfile(true);
    setProfileError(null);
    try {
      const updated = await adminApi.updateProfile({ display_name: displayName });
      setAccount(updated);
      toast('success', 'Display name updated');
    } catch {
      setProfileError('Failed to update profile.');
    } finally {
      setSavingProfile(false);
    }
  }

  async function handleChangePassword() {
    setPasswordError(null);
    if (newPassword !== confirmPassword) {
      setPasswordError('New passwords do not match.');
      return;
    }
    if (newPassword.length < 8) {
      setPasswordError('New password must be at least 8 characters.');
      return;
    }
    setSavingPassword(true);
    try {
      await adminApi.changePassword({ current_password: currentPassword, new_password: newPassword });
      toast('success', 'Password updated');
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : 'Failed to change password.');
    } finally {
      setSavingPassword(false);
    }
  }

  if (loading) {
    return (
      <div className="max-w-[600px] mx-auto">
        <Card>
          <Skeleton lines={4} />
        </Card>
      </div>
    );
  }

  return (
    <div className="max-w-[600px] mx-auto">
      <div className="mb-lg">
        <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">
          Account Settings
        </h1>
        <p className="m-0 text-sm text-text-secondary">
          Manage your admin profile and login credentials.
        </p>
      </div>

      {error && (
        <div
          className="bg-error-light border border-error/20 rounded-lg px-4 py-3 text-error text-sm mb-md"
          role="alert"
        >
          {error}
        </div>
      )}

      {/* Profile section */}
      <Card className="mb-md">
        <h2 className="mt-0 mb-md text-base font-semibold">Profile</h2>

        <div className="flex flex-col gap-md mb-md">
          <FormField label="Display Name">
            <TextInput
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Your name"
            />
          </FormField>

          <div>
            <p className="text-xs font-semibold text-text-muted uppercase tracking-wide mb-1">
              Email
            </p>
            <p className="text-sm text-text-secondary m-0">{account?.email ?? '—'}</p>
          </div>
        </div>

        {profileError && (
          <p className="text-error text-sm mb-md">{profileError}</p>
        )}

        <Button onClick={handleSaveProfile} disabled={savingProfile}>
          {savingProfile ? 'Saving…' : 'Save Profile'}
        </Button>
      </Card>

      {/* Change password section */}
      <Card>
        <h2 className="mt-0 mb-md text-base font-semibold">Change Password</h2>

        <div className="flex flex-col gap-md mb-md">
          <FormField label="Current Password" required>
            <TextInput
              type="password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              placeholder="Enter current password"
            />
          </FormField>

          <FormField label="New Password" required hint="Minimum 8 characters">
            <TextInput
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="Enter new password"
            />
          </FormField>

          <FormField label="Confirm New Password" required>
            <TextInput
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Confirm new password"
            />
          </FormField>
        </div>

        {passwordError && (
          <p className="text-error text-sm mb-md">{passwordError}</p>
        )}

        <Button
          onClick={handleChangePassword}
          disabled={savingPassword || !currentPassword || !newPassword || !confirmPassword}
        >
          {savingPassword ? 'Updating…' : 'Update Password'}
        </Button>
      </Card>
    </div>
  );
}
