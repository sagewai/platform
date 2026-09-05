'use client';

import { useState } from 'react';

import { Button } from '@/components/ui/button';
import { adminApi, artifactDigest } from '@/utils/api';

/**
 * One artifact of a Task, read through `GET /api/v1/artifacts/{digest}?task_id=`.
 *
 * The route serves only references the Task's own stream carries and refuses a `restricted`
 * Task with a 403 (spec section 19); the refusal is rendered, never worked around.
 */
export function ArtifactPanel({
  taskId,
  reference,
  slug,
  label,
}: {
  taskId: string;
  reference: string;
  slug: string;
  label: string;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const idleLabel = content === null ? `Open ${label}` : `Reload ${label}`;

  async function open() {
    setBusy(true);
    setError('');
    try {
      setContent(await adminApi.getTaskArtifact(taskId, artifactDigest(reference)));
    } catch (cause) {
      setContent(null);
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-2">
      <Button
        size="xs"
        variant="outline"
        disabled={busy}
        aria-busy={busy}
        data-testid={`artifact-open-${slug}`}
        onClick={() => void open()}
      >
        {busy ? `Opening ${label}…` : idleLabel}
      </Button>
      {error !== '' && (
        <p
          role="alert"
          data-testid={`artifact-error-${slug}`}
          className="m-0 mt-1 text-sm text-foreground"
        >
          {error}
        </p>
      )}
      {content !== null && (
        <pre
          data-testid={`artifact-body-${slug}`}
          className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-xs"
        >
          {content}
        </pre>
      )}
    </div>
  );
}
