'use client';

import { useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { adminApi } from '@/utils/api';

/**
 * Answer one open clarification, or take its default.
 *
 * `attention_id` and `attention_version` are the question as it was presented; a question
 * re-asked at a higher version refuses an answer composed against the old text, which is why
 * both travel with every body and neither is ever guessed.
 */
export function AnswerControls({
  taskId,
  attentionId,
  attentionVersion,
  defaultable,
  deadlineAt,
  onAnswered,
  onError,
}: {
  taskId: string;
  attentionId: string;
  attentionVersion: number;
  defaultable: boolean;
  deadlineAt: string | null;
  onAnswered: () => void;
  onError: (message: string) => void;
}) {
  const [answer, setAnswer] = useState('');
  const [busy, setBusy] = useState<'answer' | 'default' | null>(null);
  const busyRef = useRef(false);

  async function send(
    kind: 'answer' | 'default',
    body: { answer: string } | { use_default: true },
  ) {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(kind);
    onError('');
    try {
      await adminApi.answerTaskQuestion(taskId, {
        attention_id: attentionId,
        attention_version: attentionVersion,
        ...body,
      });
      setAnswer('');
      onAnswered();
    } catch (cause) {
      onError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(null);
    }
  }

  return (
    <div className="mt-2 space-y-2" data-testid={`answer-controls-${attentionId}`}>
      {deadlineAt !== null && (
        <p className="m-0 text-xs text-foreground">
          {defaultable
            ? `Defaults at ${new Date(deadlineAt).toLocaleString()} if nobody answers.`
            : `Waits for you until ${new Date(deadlineAt).toLocaleString()}.`}
        </p>
      )}
      <label className="flex flex-col gap-1 text-xs text-foreground">
        {`Answer to ${attentionId}`}
        <Input
          className="max-w-md"
          value={answer}
          onChange={(event) => setAnswer(event.target.value)}
        />
      </label>
      <div className="flex gap-2">
        <Button
          size="sm"
          aria-busy={busy === 'answer'}
          disabled={busy !== null || answer === ''}
          onClick={() => void send('answer', { answer })}
        >
          {busy === 'answer' ? 'Sending answer' : 'Send answer'}
        </Button>
        {defaultable && (
          <Button
            size="sm"
            variant="outline"
            aria-busy={busy === 'default'}
            disabled={busy !== null}
            onClick={() => void send('default', { use_default: true })}
          >
            {busy === 'default' ? 'Using default' : 'Use default'}
          </Button>
        )}
      </div>
    </div>
  );
}
