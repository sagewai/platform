import Link from 'next/link';

import { adminApi } from '@/utils/api';
import type { WorkflowTemplate } from '@/utils/types';
import { Card, Button, EmptyState } from '@/components/ui/legacy';

export const dynamic = 'force-dynamic';

export default async function WorkflowTemplatesPage() {
  let templates: WorkflowTemplate[] = [];
  try {
    templates = await adminApi.listWorkflowTemplates();
  } catch {
    // API unavailable
  }

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Workflow Templates</h1>

      {templates.length === 0 ? (
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
