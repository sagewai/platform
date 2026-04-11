'use client';

import { useEffect, useState, useCallback, useRef } from 'react';
import { adminApi } from '@/utils/api';
import type { Workspace } from '@/utils/types';

export function WorkspaceSwitcher() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [current, setCurrent] = useState<Workspace | null>(null);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const fetchWorkspaces = useCallback(async () => {
    try {
      const data = await adminApi.listWorkspaces();
      setWorkspaces(data);
      if (data.length > 0 && !current) {
        setCurrent(data[0]);
      }
    } catch { /* ignore */ }
  }, [current]);

  useEffect(() => { fetchWorkspaces(); }, [fetchWorkspaces]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  function handleSelect(ws: Workspace) {
    setCurrent(ws);
    setOpen(false);
  }

  return (
    <div ref={ref} className="relative mb-4">
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-3 py-2.5 bg-primary-light border border-primary/20 rounded-lg cursor-pointer text-left text-[13px] flex justify-between items-center"
      >
        <span className="font-semibold text-primary">
          {current?.name ?? 'Select workspace'}
        </span>
        <span className="text-primary text-[10px]">{open ? '\u25B2' : '\u25BC'}</span>
      </button>

      {open && (
        <div className="absolute top-full left-0 right-0 mt-1 bg-bg-surface border border-primary/20 rounded-lg shadow-lg z-50 overflow-hidden">
          {workspaces.map((ws) => (
            <button
              key={ws.id}
              onClick={() => handleSelect(ws)}
              className={`w-full px-3 py-2.5 border-none cursor-pointer text-left text-[13px] block border-b border-border ${
                ws.id === current?.id ? 'bg-primary-light' : 'bg-bg-surface hover:bg-bg-subtle'
              }`}
            >
              <div className="font-medium">{ws.name}</div>
              <div className="text-[11px] text-text-muted">{ws.slug}</div>
            </button>
          ))}
          <a
            href="/workspace/settings"
            className="block px-3 py-2.5 text-center text-[13px] text-primary no-underline border-t border-border"
          >
            + Create Workspace
          </a>
        </div>
      )}
    </div>
  );
}
