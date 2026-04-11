'use client';

import { useState, useEffect, useCallback } from 'react';
import { HelpCircle, X } from 'lucide-react';

interface HelpPanelProps {
  title: string;
  children: React.ReactNode;
}

export function HelpPanel({ title, children }: HelpPanelProps) {
  const [open, setOpen] = useState(false);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') close();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, close]);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="fixed top-4 right-4 z-40 w-9 h-9 rounded-full bg-bg-elevated text-text-on-dark flex items-center justify-center hover:bg-primary hover:text-bg-deep transition-colors shadow-lg"
        aria-label="Open help"
        title="Help"
      >
        <HelpCircle size={18} />
      </button>

      {open && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-50 bg-black/40"
            onClick={close}
            aria-hidden="true"
          />

          {/* Drawer */}
          <aside
            role="dialog"
            aria-label={title}
            className="fixed top-0 right-0 z-50 h-full w-80 bg-bg-surface border-l border-border shadow-xl overflow-y-auto"
            style={{ animation: 'slide-in-right 200ms var(--ease-enter) both' }}
          >
            <div className="flex items-center justify-between p-4 border-b border-border">
              <h2 className="text-base font-semibold m-0 font-[family-name:var(--font-heading)]">
                {title}
              </h2>
              <button
                onClick={close}
                className="w-8 h-8 flex items-center justify-center rounded-md hover:bg-bg-subtle transition-colors"
                aria-label="Close help"
              >
                <X size={16} />
              </button>
            </div>
            <div className="p-4 text-sm text-text-secondary leading-relaxed help-content">
              {children}
            </div>
          </aside>

          <style>{`
            @keyframes slide-in-right {
              from { transform: translateX(100%); }
              to   { transform: translateX(0); }
            }
            .help-content h3 { font-size: 0.875rem; font-weight: 600; color: var(--color-text-primary); margin: 1rem 0 0.5rem; }
            .help-content h3:first-child { margin-top: 0; }
            .help-content p { margin: 0.5rem 0; }
            .help-content code { font-size: 0.8125rem; font-family: var(--font-mono); background: var(--color-bg-subtle); border: 1px solid var(--color-border); border-radius: 4px; padding: 1px 5px; }
            .help-content ul { padding-left: 1.25rem; margin: 0.5rem 0; }
            .help-content li { margin: 0.25rem 0; }
          `}</style>
        </>
      )}
    </>
  );
}
