'use client';

import { Card, EmptyState } from '@sagecurator/ui';

export default function TeamsPage() {
  return (
    <div className="max-w-[640px] mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Teams</h1>
      <Card>
        <EmptyState
          title="Coming in M6"
          description="Team-based access control is planned for Milestone 6."
        />
        <p className="m-0 text-sm text-text-muted text-center">
          In the meantime, you can manage individual members from the{' '}
          <a href="/workspace/members" className="text-primary no-underline">Members</a> page.
        </p>
      </Card>
    </div>
  );
}
