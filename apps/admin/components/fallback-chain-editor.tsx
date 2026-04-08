'use client';

import { useState } from 'react';
import { Card, Button } from '@sagecurator/ui';

interface FallbackChainEditorProps {
  chain: string[];
  onChange: (chain: string[]) => void;
}

const COMMON_MODELS = [
  'gpt-4o',
  'gpt-4o-mini',
  'gpt-4-turbo',
  'claude-sonnet-4-20250514',
  'claude-3-5-haiku-20241022',
  'gemini-2.0-flash',
  'gemini-1.5-pro',
  'mistral-large',
  'deepseek-chat',
];

export function FallbackChainEditor({ chain, onChange }: FallbackChainEditorProps) {
  const [newModel, setNewModel] = useState('');

  function addModel(model: string) {
    if (model && !chain.includes(model)) {
      onChange([...chain, model]);
    }
    setNewModel('');
  }

  function removeModel(index: number) {
    const updated = chain.filter((_, i) => i !== index);
    onChange(updated);
  }

  function moveUp(index: number) {
    if (index === 0) return;
    const updated = [...chain];
    [updated[index - 1], updated[index]] = [updated[index], updated[index - 1]];
    onChange(updated);
  }

  function moveDown(index: number) {
    if (index === chain.length - 1) return;
    const updated = [...chain];
    [updated[index], updated[index + 1]] = [updated[index + 1], updated[index]];
    onChange(updated);
  }

  return (
    <Card>
      <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">
        Fallback Chain Editor
      </h3>
      <p className="mt-0 mb-md text-[13px] text-text-muted">
        When an agent exceeds its budget, the system falls back to cheaper models in this order.
      </p>

      {/* Current chain */}
      {chain.length > 0 ? (
        <div className="mb-md">
          {chain.map((model, idx) => (
            <div
              key={`${model}-${idx}`}
              className="flex items-center gap-2 px-3 py-2 mb-1 bg-bg-subtle rounded-md border border-border"
            >
              <span className="w-6 h-6 rounded-full bg-primary text-white flex items-center justify-center text-xs font-semibold shrink-0">
                {idx + 1}
              </span>
              <span className="flex-1 text-sm">{model}</span>
              <Button variant="secondary" onClick={() => moveUp(idx)} disabled={idx === 0}>
                Up
              </Button>
              <Button variant="secondary" onClick={() => moveDown(idx)} disabled={idx === chain.length - 1}>
                Down
              </Button>
              <Button variant="secondary" className="text-error border-error" onClick={() => removeModel(idx)}>
                Remove
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <p className="mt-0 mb-md text-[13px] text-text-muted italic">
          No fallback models configured.
        </p>
      )}

      {/* Add model */}
      <div className="flex gap-2">
        <select
          className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
          value={newModel}
          onChange={(e) => setNewModel(e.target.value)}
        >
          <option value="">Select a model...</option>
          {COMMON_MODELS.filter((m) => !chain.includes(m)).map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <Button onClick={() => addModel(newModel)} disabled={!newModel}>
          Add
        </Button>
      </div>
    </Card>
  );
}
