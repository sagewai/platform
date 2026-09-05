'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { CheckCircle2, Inbox } from 'lucide-react';

import { AnswerControls } from '@/components/coordinator/answer-controls';
import { GateControls } from '@/components/coordinator/gate-controls';
import { Badge } from '@/components/ui/badge';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { EmptyState } from '@/components/ui/empty-state';
import { Skeleton } from '@/components/ui/skeleton';
import { useProject } from '@/utils/project-context';
import { useWorkAttention } from '@/utils/work-attention-context';
import type { DecisionUrgency, TaskDecisionItem } from '@/utils/types';

function urgencyVariant(urgency: DecisionUrgency): 'default' | 'secondary' | 'outline' {
  if (urgency === 'now') return 'default';
  return urgency === 'today' ? 'secondary' : 'outline';
}

function isTaskItem(item: TaskDecisionItem): item is TaskDecisionItem & { task_id: string } {
  return item.task_id !== null;
}

function isWorkItem(
  item: TaskDecisionItem,
): item is TaskDecisionItem & { task_id: null; work_id: string } {
  return item.task_id === null && item.work_id !== null;
}

function rowId(item: TaskDecisionItem): string {
  return `${item.task_id ?? item.work_id}:${item.attention_id}`;
}

export default function DecisionsPage() {
  const { currentSlug, ready } = useProject();
  const { decisions, decisionsError, loading, refresh } = useWorkAttention();
  const [answerError, setAnswerError] = useState('');
  const [gateError, setGateError] = useState('');
  const needsProject = ready && currentSlug === null;

  useEffect(() => {
    setAnswerError('');
    setGateError('');
  }, [currentSlug]);

  return (
    <div className="mx-auto max-w-5xl space-y-6" data-testid="coordinator-decisions-page">
      <div>
        <h1 className="m-0 font-[family-name:var(--font-heading)] text-2xl font-bold">
          Decisions
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Everything {currentSlug ?? 'this project'} is waiting on you for, Task attention and Work
          attention merged, soonest due first.
        </p>
      </div>

      {!needsProject && decisionsError !== null && (
        <div
          role="alert"
          data-testid="decisions-error"
          className="rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-foreground"
        >
          {decisionsError}
        </div>
      )}

      {needsProject ? (
        <EmptyState
          icon={Inbox}
          title="Select a project"
          description="The decisions inbox is scoped to one project."
        />
      ) : loading ? (
        <div aria-label="Loading decisions">
          <Skeleton className="h-40 w-full" />
        </div>
      ) : decisions.length === 0 && decisionsError === null ? (
        <EmptyState
          icon={CheckCircle2}
          title="Nothing needs you"
          description="Every gate is decided and every question is answered in this project."
        />
      ) : (
        <div className="space-y-3">
          {decisions.map((item) => (
            <Card key={rowId(item)} data-testid={`decision-row-${rowId(item)}`}>
              <CardHeader className="border-b">
                <CardTitle className="flex flex-wrap items-center justify-between gap-2">
                  <h2 className="m-0 text-base font-medium">{item.summary}</h2>
                  <span className="flex items-center gap-1.5">
                    <Badge variant="outline">{item.kind}</Badge>
                    <Badge variant={urgencyVariant(item.urgency)}>
                      {item.urgency.replace('_', ' ')}
                    </Badge>
                  </span>
                </CardTitle>
                <CardDescription className="text-foreground">
                  Due {new Date(item.due_at).toLocaleString()} ·{' '}
                  {item.task_id === null ? `Work ${item.work_id}` : `Task ${item.task_id}`}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {item.evidence_refs.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {item.evidence_refs.map((reference) => (
                      <Badge key={reference} variant="outline" className="font-mono text-[10px]">
                        {reference}
                      </Badge>
                    ))}
                  </div>
                )}
                {item.gate_id !== null && item.decided_by !== null ? (
                  <GateControls
                    taskId={item.task_id}
                    workId={item.work_id}
                    gateId={item.gate_id}
                    decidedBy={item.decided_by}
                    onDecided={() => void refresh()}
                    onError={setGateError}
                  />
                ) : item.attention_version !== null && isTaskItem(item) ? (
                  <AnswerControls
                    taskId={item.task_id}
                    attentionId={item.attention_id}
                    attentionVersion={item.attention_version}
                    defaultable={false}
                    deadlineAt={null}
                    onAnswered={() => void refresh()}
                    onError={setAnswerError}
                  />
                ) : isWorkItem(item) ? (
                  <Link
                    href={`/work/${encodeURIComponent(item.work_id)}`}
                    className="text-sm text-primary no-underline hover:underline"
                  >
                    Open the Work
                  </Link>
                ) : isTaskItem(item) ? (
                  <Link
                    href={`/tasks/${encodeURIComponent(item.task_id)}`}
                    className="text-sm text-primary no-underline hover:underline"
                  >
                    Open the Task
                  </Link>
                ) : null}
              </CardContent>
            </Card>
          ))}
          {answerError !== '' && (
            <p
              role="alert"
              data-testid="decision-answer-error"
              className="m-0 text-sm text-foreground"
            >
              {answerError}
            </p>
          )}
          {gateError !== '' && (
            <p
              role="alert"
              data-testid="decision-gate-error"
              className="m-0 text-sm text-foreground"
            >
              {gateError}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
