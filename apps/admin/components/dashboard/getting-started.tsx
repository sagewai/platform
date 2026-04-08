'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { X, Check, Bot, Settings2, Play, GitBranch } from 'lucide-react';

const LS_KEY = 'sagewai-getting-started-dismissed';

interface ChecklistItem {
  id: string;
  label: string;
  href: string;
  icon: React.ElementType;
  description: string;
}

const CHECKLIST: ChecklistItem[] = [
  { id: 'llm', label: 'Configure an LLM provider', href: '/system/models', icon: Settings2, description: 'Add API keys for OpenAI, Anthropic, or other providers' },
  { id: 'agent', label: 'Create your first agent', href: '/agents', icon: Bot, description: 'Register an agent in the Agent Registry' },
  { id: 'playground', label: 'Run a test in the Playground', href: '/playground', icon: Play, description: 'Try chatting with an agent in the Playground' },
  { id: 'workflow', label: 'Set up a workflow', href: '/workflows', icon: GitBranch, description: 'Build a multi-step agent workflow' },
];

export function GettingStarted() {
  const [dismissed, setDismissed] = useState(true);
  const [completed, setCompleted] = useState<Set<string>>(new Set());

  useEffect(() => {
    try {
      const val = localStorage.getItem(LS_KEY);
      setDismissed(val === 'true');
      const doneStr = localStorage.getItem(LS_KEY + '-done');
      if (doneStr) setCompleted(new Set(JSON.parse(doneStr)));
    } catch {}
  }, []);

  function dismiss() {
    setDismissed(true);
    try { localStorage.setItem(LS_KEY, 'true'); } catch {}
  }

  function toggleItem(id: string) {
    setCompleted((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      try { localStorage.setItem(LS_KEY + '-done', JSON.stringify([...next])); } catch {}
      return next;
    });
  }

  if (dismissed) return null;

  return (
    <div className="bg-bg-surface border border-white/10 rounded-xl p-6 mb-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-semibold font-[family-name:var(--font-heading)] m-0">Getting Started</h3>
        <button onClick={dismiss} className="text-text-muted hover:text-white transition-colors cursor-pointer bg-transparent border-none">
          <X size={16} />
        </button>
      </div>
      <div className="space-y-3">
        {CHECKLIST.map((item) => {
          const done = completed.has(item.id);
          const Icon = item.icon;
          return (
            <div key={item.id} className="flex items-start gap-3">
              <button
                onClick={() => toggleItem(item.id)}
                className={`mt-0.5 w-5 h-5 rounded-md border flex items-center justify-center flex-shrink-0 cursor-pointer transition-colors bg-transparent ${
                  done ? 'bg-primary border-primary' : 'border-white/20 hover:border-primary/50'
                }`}
              >
                {done && <Check size={12} className="text-white" />}
              </button>
              <div>
                <Link href={item.href} className={`text-sm font-medium no-underline transition-colors ${done ? 'text-text-muted line-through' : 'text-white hover:text-primary'}`}>
                  {item.label}
                </Link>
                <p className="text-xs text-text-muted m-0 mt-0.5">{item.description}</p>
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-text-muted mt-4 mb-0">
        {completed.size}/{CHECKLIST.length} completed
      </p>
    </div>
  );
}
