'use client';

import { useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { adminApi } from '@/utils/api';

/**
 * Decide one gate, on the route its `decided_by` names.
 *
 * `TaskService.decide_gate` accepts the Task's own gate classes and refuses a Work's gate,
 * naming `POST /api/v1/work/{work_id}/gates/{gate_id}`. Which one owns a gate is a field, not
 * something to read out of a gate id. The Work route decides `merge:` gates only, so the control
 * points every other Work gate at `sagewai work approve` instead of rendering buttons.
 */
export async function decideGate(args: {
  taskId: string | null;
  workId: string | null;
  gateId: string;
  decidedBy: 'task' | 'work';
  decision: 'allow' | 'deny';
  note: string | null;
}): Promise<void> {
  const body = { decision: args.decision, note: args.note };
  if (args.decidedBy === 'work') {
    if (args.workId === null) {
      throw new Error(`gate ${args.gateId} is decided on its Work, which this view does not name`);
    }
    await adminApi.decideWorkGate(args.workId, args.gateId, body);
    return;
  }
  if (args.taskId === null) {
    throw new Error(`gate ${args.gateId} is decided on its Task, which this view does not name`);
  }
  await adminApi.decideTaskGate(args.taskId, args.gateId, body);
}

export function GateControls({
  taskId,
  workId,
  gateId,
  decidedBy,
  onDecided,
  onError,
}: {
  taskId: string | null;
  workId: string | null;
  gateId: string;
  decidedBy: 'task' | 'work';
  onDecided: () => void;
  onError: (message: string) => void;
}) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<'allow' | 'deny' | null>(null);
  const busyRef = useRef(false);

  if (decidedBy === 'work' && !gateId.startsWith('merge:')) {
    return <p className="m-0 mt-2 text-sm">decide with sagewai work approve</p>;
  }

  async function decide(decision: 'allow' | 'deny') {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(decision);
    onError('');
    try {
      await decideGate({
        taskId,
        workId,
        gateId,
        decidedBy,
        decision,
        note: note === '' ? null : note,
      });
      setNote('');
      onDecided();
    } catch (cause) {
      onError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  }

  return (
    <div className="mt-2 space-y-2" data-testid={`gate-controls-${gateId}`}>
      <label className="flex flex-col gap-1 text-xs text-foreground">
        {`Note for ${gateId}`}
        <Input
          className="max-w-md"
          value={note}
          placeholder="Optional note recorded on the thread"
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      <div className="flex gap-2">
        <Button
          size="sm"
          aria-busy={busy === 'allow'}
          disabled={busy !== null}
          onClick={() => void decide('allow')}
        >
          {busy === 'allow' ? 'Allowing' : 'Allow'}
        </Button>
        <Button
          size="sm"
          variant="outline"
          aria-busy={busy === 'deny'}
          disabled={busy !== null}
          onClick={() => void decide('deny')}
        >
          {busy === 'deny' ? 'Denying' : 'Deny'}
        </Button>
      </div>
    </div>
  );
}
