// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (C) 2026 Ali Arda Diri
'use client';

import { useEffect, useState } from 'react';

export function GoalToBlueprintIllustration({ className }: { className?: string }) {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setVisible(true), 60);
    return () => clearTimeout(id);
  }, []);

  const stages = [
    {
      label: 'Goal',
      icon: (
        <path
          d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 14H9V8h2v8zm4 0h-2V8h2v8z"
          fill="currentColor"
        />
      ),
      color: 'text-primary',
      bgColor: 'bg-primary/10',
      desc: 'Plain English',
    },
    {
      label: 'Blueprint',
      icon: (
        <path
          d="M19 3H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2V5c0-1.1-.9-2-2-2zm-7 14l-5-5 1.41-1.41L12 14.17l7.59-7.59L21 8l-9 9z"
          fill="currentColor"
        />
      ),
      color: 'text-success',
      bgColor: 'bg-success/10',
      desc: 'Agent design',
    },
    {
      label: 'Mission',
      icon: (
        <>
          <circle cx="12" cy="12" r="3" fill="currentColor" />
          <path d="M12 2v4M12 18v4M4.22 4.22l2.83 2.83M16.95 16.95l2.83 2.83M2 12h4M18 12h4M4.22 19.78l2.83-2.83M16.95 7.05l2.83-2.83" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
        </>
      ),
      color: 'text-warning',
      bgColor: 'bg-warning/10',
      desc: 'Live execution',
    },
  ];

  return (
    <div
      className={`flex flex-col items-center gap-2 select-none ${className ?? ''}`}
      aria-hidden="true"
      data-testid="goal-to-blueprint-illustration"
    >
      {stages.map((stage, i) => (
        <div key={stage.label} className="flex flex-col items-center w-full">
          <div
            className={`flex items-center gap-3 w-full rounded-xl border border-border p-3 transition-all duration-500 ${
              visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-3'
            }`}
            style={{ transitionDelay: `${i * 120}ms` }}
          >
            <div className={`rounded-lg ${stage.bgColor} ${stage.color} p-2 shrink-0`}>
              <svg viewBox="0 0 24 24" className="size-5">
                {stage.icon}
              </svg>
            </div>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-text-primary m-0">{stage.label}</p>
              <p className="text-xs text-text-muted m-0">{stage.desc}</p>
            </div>
          </div>

          {i < stages.length - 1 && (
            <div
              className={`w-px h-5 bg-border transition-all duration-300 ${
                visible ? 'opacity-100' : 'opacity-0'
              }`}
              style={{ transitionDelay: `${i * 120 + 100}ms` }}
            />
          )}
        </div>
      ))}
    </div>
  );
}
