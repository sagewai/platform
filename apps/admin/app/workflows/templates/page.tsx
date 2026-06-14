'use client';

// Templates fetch authenticated control-plane data, so this must run in the
// BROWSER, not as a Server Component. Server-side rendering happens inside the
// admin container, where (a) `localhost:8000` is the admin itself rather than the
// backend, and (b) there is no access to the user's bearer token (it lives in
// browser storage) — so the call fails/401 and the list comes back empty.
// Fetching client-side reuses the same auth + host the other admin pages use.
// See app/page.tsx (PR #468) for the matching dashboard fix.

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';

import { adminApi } from '@/utils/api';
import type { WorkflowTemplate } from '@/utils/types';
import { Card, Button, EmptyState } from '@/components/ui/legacy';

export default function WorkflowTemplatesPage() {
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setTemplates(await adminApi.listWorkflowTemplates());
    } catch {
      // API unavailable — leave templates empty.
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Workflow Templates</h1>

      {loading ? (
        <div className="text-sm text-text-muted">Loading templates…</div>
      ) : templates.length === 0 ? (
        <EmptyState title="No Templates" description="No workflow templates available." />
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-md">
          {templates.map((t) => (
            <Card key={t.name} className="flex flex-col gap-3">
              <h3 className="m-0 text-base font-semibold font-[family-name:var(--font-heading)]">{t.name}</h3>

              <p className="m-0 text-sm text-text-secondary leading-relaxed flex-1">
                {t.description}
              </p>

              <Link href={`/workflows?template=${encodeURIComponent(t.name)}`}>
                <Button className="w-full text-center">Use Template</Button>
              </Link>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
