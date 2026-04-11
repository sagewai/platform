'use client';

import { Fragment, useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Sparkles, PanelLeftClose, Bot, Sun } from 'lucide-react';
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command';
import { useSidebar } from '@/components/ui/legacy';
import { ALL_GROUPS } from './nav-sidebar';

const OPEN_EVENT = 'sagewai:open-command-palette';

/** Programmatic open from anywhere (e.g. the sidebar Search button). */
export function openCommandPalette() {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event(OPEN_EVENT));
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const { expanded, setExpanded } = useSidebar();

  // ⌘K / Ctrl+K toggle + custom event listener.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === 'k' || e.key === 'K') && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener('keydown', onKey);
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener(OPEN_EVENT, onOpen);
    };
  }, []);

  const go = useCallback(
    (href: string) => {
      setOpen(false);
      router.push(href);
    },
    [router],
  );

  const toggleTheme = () => {
    const root = document.documentElement;
    const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    root.classList.toggle('dark', next === 'dark');
    localStorage.setItem('sagewai-theme', next);
    setOpen(false);
  };

  // Note: we intentionally show ALL nav groups in the palette (not role-filtered).
  // Routes that the user can't access will redirect to login or show their own
  // permission gate — the palette is pure navigation, not an authorization layer.
  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Search pages or run a command…" />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>

        <CommandGroup heading="Quick actions">
          <CommandItem value="open playground" onSelect={() => go('/playground')}>
            <Sparkles className="mr-2 h-4 w-4" />
            Open Playground
          </CommandItem>
          <CommandItem value="browse agents" onSelect={() => go('/agents')}>
            <Bot className="mr-2 h-4 w-4" />
            Browse agents
          </CommandItem>
          <CommandItem
            value="toggle sidebar"
            onSelect={() => {
              setExpanded(!expanded);
              setOpen(false);
            }}
          >
            <PanelLeftClose className="mr-2 h-4 w-4" />
            Toggle sidebar
          </CommandItem>
          <CommandItem value="toggle theme dark light" onSelect={toggleTheme}>
            <Sun className="mr-2 h-4 w-4" />
            Toggle theme
          </CommandItem>
        </CommandGroup>

        {ALL_GROUPS.map((group) => {
          const Icon = group.icon;
          return (
            <Fragment key={group.id}>
              <CommandSeparator />
              <CommandGroup heading={group.label}>
                {group.items.map((item) => (
                  <CommandItem
                    key={`${group.id}:${item.href}`}
                    value={`${group.label} ${item.label} ${item.href}`}
                    onSelect={() => go(item.href)}
                  >
                    <Icon className="mr-2 h-4 w-4 opacity-70" />
                    {item.label}
                  </CommandItem>
                ))}
              </CommandGroup>
            </Fragment>
          );
        })}
      </CommandList>
    </CommandDialog>
  );
}
