'use client';

import { useEffect, useState } from 'react';
import { Sun, Moon } from 'lucide-react';
import { toast } from 'sonner';

const STORAGE_KEY = 'sagewai-theme';

function getInitialTheme(): 'light' | 'dark' {
  if (typeof window === 'undefined') return 'dark';
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === 'dark' || stored === 'light') return stored;
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

function applyTheme(theme: 'light' | 'dark') {
  const root = document.documentElement;
  root.setAttribute('data-theme', theme);
  // shadcn's `.dark` class is the second source of truth — keep them in lock-step.
  root.classList.toggle('dark', theme === 'dark');
}

/**
 * Theme toggle — drives both the legacy `data-theme="dark"` attribute (used by
 * the brand tokens in tokens/src/index.css) and the shadcn `.dark` class (used
 * by the @custom-variant in globals.css). Both must move together so the brand
 * tokens and the shadcn primitives stay in sync.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<'light' | 'dark'>('dark');
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    const initial = getInitialTheme();
    setTheme(initial);
    applyTheme(initial);
  }, []);

  const toggle = () => {
    const next = theme === 'light' ? 'dark' : 'light';
    setTheme(next);
    applyTheme(next);
    localStorage.setItem(STORAGE_KEY, next);
    toast.success(`Switched to ${next} mode`, { duration: 1500 });
  };

  if (!mounted) return <div className="w-8 h-8" />;

  return (
    <button
      onClick={toggle}
      aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`}
      className="w-8 h-8 flex items-center justify-center rounded-md text-sidebar-muted-foreground hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors cursor-pointer bg-transparent border-none"
    >
      {theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}
    </button>
  );
}
