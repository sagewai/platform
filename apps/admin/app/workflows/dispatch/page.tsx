'use client';

import { useState } from 'react';
import Link from 'next/link';
import { Card, Button } from '@/components/ui/legacy';
import { HelpCircle, Play, AlertTriangle } from 'lucide-react';

export default function DispatchPage() {
  const [workflowName, setWorkflowName] = useState('');
  const [inputJson, setInputJson] = useState('{}');
  const [priority, setPriority] = useState(0);
  const [showHelp, setShowHelp] = useState(false);

  // Legacy by-name dispatch is not implemented in this build (the backend
  // returns 501). Enqueue a workflow from the builder or via the API instead.
  // The form is left visible but inert so the navigation entry stays honest.

  return (
    <div className="max-w-[42rem] mx-auto">
      <div className="flex items-center justify-between mb-lg">
        <div>
          <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)]">
            Dispatch Workflow
          </h1>
          <p className="text-sm text-text-secondary mt-1">
            Manually enqueue a workflow for execution by a worker
          </p>
        </div>
        <button
          onClick={() => setShowHelp(!showHelp)}
          className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded border border-border hover:bg-bg-subtle"
          title="Help"
        >
          <HelpCircle className="w-3.5 h-3.5" />
          Help
        </button>
      </div>

      <Card>
        <div className="p-md mb-lg flex items-start gap-3 text-sm">
          <AlertTriangle className="w-5 h-5 shrink-0 text-warning mt-0.5" />
          <div className="space-y-1">
            <p className="font-medium text-text-primary m-0">
              Not available in this build
            </p>
            <p className="text-text-secondary m-0">
              Legacy by-name dispatch is not implemented. Enqueue a workflow from
              the{' '}
              <Link href="/workflows" className="text-primary underline">
                workflow builder
              </Link>{' '}
              or POST to <code className="text-[13px]">/api/v1/workflows/enqueue</code>.
            </p>
          </div>
        </div>
      </Card>

      {showHelp && (
        <Card>
          <div className="p-md text-sm text-text-secondary space-y-2 mb-lg">
            <p>This legacy page is retained for reference only. Workflows are enqueued from the builder or via <code className="text-[13px]">/api/v1/workflows/enqueue</code>; provide input data as JSON and set priority to control execution order in the queue.</p>
          </div>
        </Card>
      )}

      <Card>
        <fieldset disabled className="opacity-60 cursor-not-allowed">
          <div className="p-md space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1">Workflow Name</label>
              <input
                type="text"
                value={workflowName}
                onChange={(e) => setWorkflowName(e.target.value)}
                placeholder="e.g. article-pipeline"
                className="w-full px-3 py-2 text-sm rounded border border-border bg-bg-surface focus:outline-none focus:border-primary"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Input (JSON)</label>
              <textarea
                value={inputJson}
                onChange={(e) => setInputJson(e.target.value)}
                className="w-full px-3 py-2 text-sm rounded border border-border bg-bg-surface font-[family-name:var(--font-mono)] text-[13px] focus:outline-none focus:border-primary"
                rows={6}
                spellCheck={false}
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">Priority</label>
              <select
                value={priority}
                onChange={(e) => setPriority(Number(e.target.value))}
                className="px-3 py-2 text-sm rounded border border-border bg-bg-surface focus:outline-none focus:border-primary"
              >
                <option value={0}>Normal (0)</option>
                <option value={1}>High (1)</option>
                <option value={2}>Urgent (2)</option>
                <option value={5}>Critical (5)</option>
              </select>
            </div>

            <Button disabled title="Not available in this build — use the workflow builder or /api/v1/workflows/enqueue">
              <Play className="w-4 h-4 mr-1.5" />
              Dispatch Workflow
            </Button>
          </div>
        </fieldset>
      </Card>
    </div>
  );
}
