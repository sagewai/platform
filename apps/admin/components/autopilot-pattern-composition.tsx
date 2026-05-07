'use client';

import { ArrowDownRight, Box, Layers } from 'lucide-react';
import type { BlueprintCompositionStep } from '@/utils/types';

interface AutopilotPatternCompositionProps {
  composition: BlueprintCompositionStep[];
}

function InputPair({ label, value }: { label: string; value: unknown }) {
  const display =
    typeof value === 'object' && value !== null
      ? JSON.stringify(value)
      : String(value ?? '');
  return (
    <div className="flex gap-2 min-w-0">
      <dt className="shrink-0 text-[11px] font-semibold uppercase tracking-wide text-text-muted w-28 truncate">
        {label}
      </dt>
      <dd className="m-0 text-xs text-text-primary font-[family-name:var(--font-mono)] truncate flex-1">
        {display || <span className="text-text-muted italic">—</span>}
      </dd>
    </div>
  );
}

export function AutopilotPatternComposition({
  composition,
}: AutopilotPatternCompositionProps) {
  if (composition.length === 0) {
    return (
      <p className="text-sm text-text-muted italic">No composition steps defined.</p>
    );
  }

  return (
    <ol className="space-y-3 list-none m-0 p-0">
      {composition.map((step, idx) => {
        const inputEntries = Object.entries(step.inputs);
        return (
          <li key={`${step.pattern}-${idx}`}>
            <div className="bg-bg-subtle border border-border rounded-lg overflow-hidden">
              {/* Step header */}
              <div className="flex items-center gap-2 px-4 py-2.5 bg-bg-surface border-b border-border">
                <span className="shrink-0 w-5 h-5 rounded-full bg-primary/10 text-primary text-[10px] font-bold flex items-center justify-center">
                  {idx + 1}
                </span>
                {step.wraps ? (
                  <Layers size={13} className="text-text-muted shrink-0" />
                ) : (
                  <Box size={13} className="text-text-muted shrink-0" />
                )}
                <span className="text-sm font-semibold text-text-primary font-[family-name:var(--font-mono)]">
                  {step.pattern}
                </span>
                {step.wraps && (
                  <span className="ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-text-secondary bg-bg-subtle border border-border px-2 py-0.5 rounded-full">
                    <ArrowDownRight size={10} />
                    wraps&nbsp;
                    <span className="font-[family-name:var(--font-mono)] text-text-primary">
                      {step.wraps}
                    </span>
                  </span>
                )}
              </div>

              {/* Inputs */}
              {inputEntries.length > 0 ? (
                <dl className="px-4 py-3 space-y-1.5">
                  {inputEntries.map(([key, val]) => (
                    <InputPair key={key} label={key} value={val} />
                  ))}
                </dl>
              ) : (
                <p className="px-4 py-2.5 text-xs text-text-muted italic m-0">
                  No inputs.
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
