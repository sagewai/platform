'use client';

import { useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useToast } from '@/components/ui/legacy';
import { Textarea } from '@/components/ui/textarea';
import { adminApi } from '@/utils/api';
import { useProject } from '@/utils/project-context';
import type { IntakePreview, TaskDefaults, TaskTemplateSummary } from '@/utils/types';

function targetLabel(defaults: TaskDefaults): string {
  if (defaults.target === null) return 'no default target';
  return defaults.target.kind === 'software'
    ? `${defaults.target.owner}/${defaults.target.repo} @ ${defaults.target.default_branch}`
    : `report with ${defaults.target.sinks.length} sink(s)`;
}

export function TaskComposer({ onCreated }: { onCreated: () => void }) {
  const { currentSlug, ready } = useProject();
  const { toast } = useToast();
  const [brief, setBrief] = useState('');
  const [preview, setPreview] = useState<IntakePreview | null>(null);
  const [templates, setTemplates] = useState<TaskTemplateSummary[]>([]);
  const [defaults, setDefaults] = useState<TaskDefaults | null>(null);
  const [pending, setPending] = useState<'preview' | 'create' | null>(null);
  const busyRef = useRef(false);
  const [error, setError] = useState('');
  const briefRef = useRef(brief);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  briefRef.current = brief;

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    Promise.all([adminApi.listTaskTemplates(), adminApi.getTaskDefaults()])
      .then(([catalogue, projectDefaults]) => {
        if (cancelled) return;
        setTemplates(catalogue.templates);
        setDefaults(projectDefaults);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, currentSlug]);

  function editBrief(value: string) {
    briefRef.current = value;
    setBrief(value);
    setPreview(null);
  }

  async function runPreview() {
    if (busyRef.current) return;
    busyRef.current = true;
    const issued = brief;
    setPending('preview');
    setError('');
    try {
      const next = await adminApi.previewTaskIntake(issued);
      if (issued === briefRef.current) setPreview(next);
    } catch (cause) {
      if (issued === briefRef.current) setError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setPending(null);
    }
  }

  async function create() {
    if (busyRef.current) return;
    busyRef.current = true;
    setPending('create');
    setError('');
    try {
      const created = await adminApi.createTask(brief);
      toast('success', `Task ${created.record.task_id} created`);
      briefRef.current = '';
      setBrief('');
      setPreview(null);
      textareaRef.current?.focus();
      onCreated();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setPending(null);
    }
  }

  async function readFile(file: File | undefined) {
    if (file === undefined) return;
    editBrief(await file.text());
  }

  const previewedKind =
    preview === null
      ? ''
      : (templates.find((template) => template.id === preview.template_id)?.kind ?? 'unknown kind');

  return (
    <Card data-testid="task-composer">
      <CardHeader className="border-b">
        <CardTitle>
          <h2 className="m-0 text-base font-medium">New Task</h2>
        </CardTitle>
        <CardDescription className="text-foreground">
          Write the brief or drop a Markdown file. Preview shows what intake would do before
          anything is created.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            void readFile(event.dataTransfer.files[0]);
          }}
        >
          <label className="flex flex-col gap-1 text-xs">
            Brief
            <Textarea
              ref={textareaRef}
              rows={4}
              value={brief}
              placeholder="Describe the Task, or drop a Markdown file here."
              onChange={(event) => editBrief(event.target.value)}
            />
          </label>
        </div>
        <label className="flex flex-col gap-1 text-xs">
          Markdown file
          <input
            type="file"
            accept=".md,text/markdown,text/plain"
            className="text-sm"
            onChange={(event) => void readFile(event.target.files?.[0])}
          />
        </label>

        {preview !== null && (
          <div
            data-testid="intake-preview"
            className="rounded-lg border border-border bg-muted/40 p-3 text-sm"
          >
            <p className="m-0">
              Template <strong>{preview.template_id}</strong> v{preview.template_version} ·{' '}
              {previewedKind} · {preview.band} at {Math.round(preview.confidence * 100)}%
              confidence
            </p>
            <p className="m-0 mt-1">
              {preview.cron === null
                ? 'Schedule: runs once'
                : `Schedule: ${preview.cron} (${preview.timezone})`}
            </p>
            <p className="m-0 mt-2 whitespace-pre-wrap">{preview.preview}</p>
            {Object.keys(preview.slots).length > 0 && (
              <p className="m-0 mt-2 text-xs">
                Slots:{' '}
                {Object.entries(preview.slots)
                  .map(([key, value]) => `${key}=${String(value)}`)
                  .join(', ')}
              </p>
            )}
            {preview.questions.length > 0 && (
              <>
                <p className="m-0 mt-2 font-medium">It will ask:</p>
                <ul className="m-0 mt-1 list-disc pl-5">
                  {preview.questions.map((question) => (
                    <li key={question.id}>{question.text}</li>
                  ))}
                </ul>
              </>
            )}
            {defaults !== null && (
              <p className="m-0 mt-2 text-xs">
                Target and execution come from the project defaults: {targetLabel(defaults)} ·{' '}
                {defaults.execution.route}.
              </p>
            )}
          </div>
        )}

        {error !== '' && (
          <p data-testid="composer-error" role="alert" className="m-0 text-sm text-destructive">
            {error}
          </p>
        )}

        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={brief.trim() === ''}
            aria-busy={pending === 'preview'}
            onClick={() => void runPreview()}
          >
            {pending === 'preview' ? 'Previewing…' : 'Preview'}
          </Button>
          <Button
            disabled={preview === null}
            aria-busy={pending === 'create'}
            onClick={() => void create()}
          >
            {pending === 'create' ? 'Creating…' : 'Create Task'}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
