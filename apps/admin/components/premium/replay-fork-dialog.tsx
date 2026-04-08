'use client';

import { useRouter } from 'next/navigation';
import { Card, Button } from '@sagecurator/ui';
import { GitBranch } from 'lucide-react';
import type { WorkflowEvent } from '@/utils/types';

interface Props {
  event: WorkflowEvent | null;
  onClose: () => void;
}

export function ReplayForkDialog({ event, onClose }: Props) {
  const router = useRouter();

  if (!event) return null;

  const outputJson = JSON.stringify(event.data, null, 2);

  function handleFork() {
    const encoded = btoa(encodeURIComponent(outputJson));
    router.push(`/workflows/dispatch?input=${encoded}`);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <Card className="max-w-lg w-full mx-4 !p-0">
        <div className="p-4 border-b border-border">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <GitBranch size={16} className="text-primary" />
            Fork from step
          </h3>
          <p className="text-xs text-text-muted mt-1">
            Create a new workflow run starting from this step&apos;s output.
          </p>
        </div>

        <div className="p-4">
          <div className="text-[10px] text-text-muted uppercase mb-1">Step Output</div>
          <pre className="text-xs bg-bg-subtle p-3 rounded max-h-48 overflow-auto whitespace-pre-wrap font-[family-name:var(--font-mono)]">
            {outputJson}
          </pre>
        </div>

        <div className="flex justify-end gap-2 p-4 border-t border-border">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={handleFork}>
            <GitBranch size={13} className="mr-1" />
            Fork
          </Button>
        </div>
      </Card>
    </div>
  );
}
