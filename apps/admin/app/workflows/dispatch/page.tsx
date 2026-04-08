'use client';

import { useState } from 'react';
import { Card, Button } from '@sagecurator/ui';
import { adminApi } from '@/utils/api';
import { HelpCircle, Play, CheckCircle } from 'lucide-react';

export default function DispatchPage() {
  const [workflowName, setWorkflowName] = useState('');
  const [inputJson, setInputJson] = useState('{}');
  const [priority, setPriority] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ run_id: string; is_new: boolean } | null>(null);
  const [error, setError] = useState('');
  const [showHelp, setShowHelp] = useState(false);

  const handleSubmit = async () => {
    setError('');
    setResult(null);

    if (!workflowName.trim()) {
      setError('Workflow name is required');
      return;
    }

    let inputData: Record<string, unknown>;
    try {
      inputData = JSON.parse(inputJson);
    } catch {
      setError('Invalid JSON input');
      return;
    }

    setSubmitting(true);
    try {
      const res = await adminApi.dispatchWorkflow(workflowName.trim(), inputData, priority);
      setResult(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Dispatch failed');
    } finally {
      setSubmitting(false);
    }
  };

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

      {showHelp && (
        <Card>
          <div className="p-md text-sm text-text-secondary space-y-2 mb-lg">
            <p>Manually enqueue a workflow by name. The workflow must be registered via YAML or the API. Provide input data as JSON and set priority to control execution order in the queue.</p>
          </div>
        </Card>
      )}

      <Card>
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

          {error && (
            <p className="text-sm text-error">{error}</p>
          )}

          {result && (
            <div className="flex items-center gap-2 text-sm text-success">
              <CheckCircle className="w-4 h-4" />
              Enqueued: <code className="text-[13px]">{result.run_id}</code>
              {!result.is_new && <span className="text-text-muted">(already existed)</span>}
            </div>
          )}

          <Button onClick={handleSubmit} disabled={submitting}>
            <Play className="w-4 h-4 mr-1.5" />
            {submitting ? 'Dispatching...' : 'Dispatch Workflow'}
          </Button>
        </div>
      </Card>
    </div>
  );
}
