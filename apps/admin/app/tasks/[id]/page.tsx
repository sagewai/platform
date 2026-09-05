'use client';

import { use, useEffect, useRef, useState } from 'react';

import { AnswerControls } from '@/components/coordinator/answer-controls';
import { ArtifactPanel } from '@/components/coordinator/artifact-panel';
import { GateControls } from '@/components/coordinator/gate-controls';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { useTaskFeed } from '@/hooks/use-task-feed';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { TaskThread, ThreadEntry } from '@/utils/types';

function entryClass(entry: ThreadEntry): string {
  if (entry.kind === 'gate') return 'border-l-4 border-l-amber-500';
  if (entry.kind === 'question') return 'border-l-4 border-l-blue-500';
  if (entry.kind === 'status') return 'border-l-4 border-l-border';
  return 'border-l-4 border-l-transparent';
}

/** 128 random bits as hex. `crypto.randomUUID` is secure-context only; this is not. */
function idempotencyKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export default function TaskThreadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { currentSlug, ready } = useProject();
  const feed = useTaskFeed(id, ready && currentSlug !== null);
  const [thread, setThread] = useState<TaskThread | null>(null);
  const [error, setError] = useState('');
  const [reloads, setReloads] = useState(0);
  const [answerError, setAnswerError] = useState('');
  const [gateError, setGateError] = useState('');
  const [message, setMessage] = useState('');
  const [messageError, setMessageError] = useState('');
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const messageKey = useRef<string | null>(null);
  const identityRef = useRef('');

  async function sendMessage() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setMessageError('');
    messageKey.current = messageKey.current ?? idempotencyKey();
    try {
      await adminApi.postTaskMessage(id, message, messageKey.current);
      messageKey.current = null;
      setMessage('');
      setReloads((count) => count + 1);
    } catch (cause) {
      setMessageError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!ready || currentSlug === null) return;
    let cancelled = false;
    const identity = `${currentSlug}:${id}`;
    if (identityRef.current !== identity) {
      identityRef.current = identity;
      setThread(null);
      setAnswerError('');
      setGateError('');
      setMessageError('');
      setMessage('');
      messageKey.current = null;
    }
    setError('');
    adminApi
      .getTaskThread(id)
      .then((loaded) => {
        if (!cancelled) setThread(loaded);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, currentSlug, id, feed.revision, reloads]);

  if (thread === null) {
    return error === '' ? (
      <Skeleton aria-label="Loading Task thread" className="h-64 w-full" />
    ) : (
      <div
        role="alert"
        data-testid="task-thread-error"
        className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-foreground"
      >
        {error}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="border-b">
          <CardTitle>
            <h2 className="m-0 text-base font-medium">Thread</h2>
          </CardTitle>
          <CardDescription className="text-foreground">
            The brief, questions, coordinator messages, gates, and outputs in the order the
            durable stream recorded them.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {thread.brief_ref !== null && (
            <ArtifactPanel taskId={id} reference={thread.brief_ref} slug="brief" label="brief" />
          )}
          <ol className="m-0 mt-4 list-none space-y-3 p-0">
            {thread.entries.map((entry) => (
              <li
                key={entry.id}
                data-testid={`thread-entry-${entry.id}`}
                className={`rounded-lg border border-border p-3 ${entryClass(entry)}`}
              >
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
                  <span>
                    #{entry.sequence} · {entry.author}
                    {entry.actor_ref === null ? '' : ` · ${entry.actor_ref}`}
                  </span>
                  <span>{new Date(entry.at).toLocaleString()}</span>
                </div>
                <p className="m-0 mt-1 whitespace-pre-wrap text-sm">{entry.text}</p>
                {entry.kind === 'question' &&
                  entry.attention_id !== null &&
                  entry.attention_version !== null &&
                  (entry.answer !== null || entry.answered_by !== null ? (
                    <p className="m-0 mt-2 text-sm">
                      {entry.answered_by === 'default' ? 'Defaulted: ' : 'Answered: '}
                      {entry.answer ?? 'the recorded default'}
                    </p>
                  ) : (
                    !entry.closed && (
                      <AnswerControls
                        taskId={id}
                        attentionId={entry.attention_id}
                        attentionVersion={entry.attention_version}
                        defaultable={entry.defaultable === true}
                        deadlineAt={entry.deadline_at}
                        onAnswered={() => setReloads((count) => count + 1)}
                        onError={setAnswerError}
                      />
                    )
                  ))}
                {entry.kind === 'gate' &&
                  entry.gate_id !== null &&
                  (entry.decision !== null ? (
                    <p className="m-0 mt-2 text-sm">Decided: {entry.decision}</p>
                  ) : (
                    !entry.closed &&
                    entry.decided_by !== null && (
                      <GateControls
                        taskId={id}
                        workId={entry.work_id}
                        gateId={entry.gate_id}
                        decidedBy={entry.decided_by}
                        onDecided={() => setReloads((count) => count + 1)}
                        onError={setGateError}
                      />
                    )
                  ))}
                {entry.refs.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {entry.refs.map((reference) => (
                      <Badge key={reference} variant="outline" className="font-mono text-[10px]">
                        {reference}
                      </Badge>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ol>
          {answerError !== '' && (
            <p
              role="alert"
              data-testid="answer-error"
              className="m-0 mt-3 text-sm text-foreground"
            >
              {answerError}
            </p>
          )}
          {gateError !== '' && (
            <p
              role="alert"
              data-testid="gate-error"
              className="m-0 mt-3 text-sm text-foreground"
            >
              {gateError}
            </p>
          )}
          {messageError !== '' && (
            <p
              role="alert"
              data-testid="message-error"
              className="m-0 mt-3 text-sm text-foreground"
            >
              {messageError}
            </p>
          )}
          <div className="mt-4 space-y-2 border-t border-border pt-4">
            <label className="flex flex-col gap-1 text-xs text-foreground">
              Message
              <Textarea
                rows={3}
                value={message}
                placeholder="Say something to the coordinator; it is recorded on the thread."
                onChange={(event) => setMessage(event.target.value)}
              />
            </label>
            <Button
              aria-busy={busy}
              disabled={busy || message.trim() === ''}
              onClick={() => void sendMessage()}
            >
              {busy ? 'Sending message' : 'Send message'}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
