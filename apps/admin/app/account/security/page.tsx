'use client';

import { useState } from 'react';
import { Card, Button } from '@/components/ui/legacy';

export default function SecurityPage() {
  const [enabled, setEnabled] = useState(false);

  return (
    <div className="space-y-6">
      <Card>
        <h2 className="text-lg font-semibold mb-4">Two-Factor Authentication</h2>
        <p className="text-text-secondary text-sm mb-4">
          Add an extra layer of security to your account with TOTP-based two-factor authentication.
        </p>
        {enabled ? (
          <div className="flex items-center gap-3">
            <div className="w-2 h-2 rounded-full bg-success" />
            <span className="text-sm text-success font-medium">2FA is enabled</span>
            <Button onClick={() => setEnabled(false)} className="ml-auto">
              Disable 2FA
            </Button>
          </div>
        ) : (
          <Button onClick={() => setEnabled(true)}>
            Enable 2FA
          </Button>
        )}
      </Card>

      <Card>
        <h2 className="text-lg font-semibold mb-4">Change Password</h2>
        <p className="text-text-secondary text-sm mb-4">
          Update your password. You will be logged out of all other sessions.
        </p>
        <div className="space-y-4 max-w-md">
          <div>
            <label className="block text-sm font-medium mb-1.5 text-text-secondary">Current Password</label>
            <input type="password" className="w-full bg-bg-surface border border-border rounded-lg px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1.5 text-text-secondary">New Password</label>
            <input type="password" className="w-full bg-bg-surface border border-border rounded-lg px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1.5 text-text-secondary">Confirm New Password</label>
            <input type="password" className="w-full bg-bg-surface border border-border rounded-lg px-3 py-2 text-sm" />
          </div>
          <Button>Update Password</Button>
        </div>
      </Card>
    </div>
  );
}
