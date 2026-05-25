// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { useState } from 'react';

const CANONICAL_TAGS = [
  'payments', 'crm', 'comms', 'observability', 'music', 'email',
  'health', 'ecommerce', 'travel', 'media',
];

type Props = {
  selected: string[];
  knownTags: string[]; // canonicals + previously-used custom tags from current connections
  onChange: (tags: string[]) => void;
};

export function TagMultiSelect({ selected, knownTags, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');

  const suggestions = Array.from(new Set([...CANONICAL_TAGS, ...knownTags]))
    .filter(t => !selected.includes(t))
    .filter(t => t.toLowerCase().includes(input.toLowerCase()));

  const add = (tag: string) => {
    if (!selected.includes(tag)) onChange([...selected, tag]);
    setInput('');
  };
  const remove = (tag: string) => onChange(selected.filter(t => t !== tag));

  return (
    <div className="relative" data-testid="tag-multi-select">
      <div className="flex min-h-[2.25rem] flex-wrap items-center gap-1 rounded border border-border bg-bg p-1">
        {selected.map(tag => (
          <span key={tag} className="rounded-full bg-info/10 px-2 py-0.5 text-xs text-info">
            {tag}
            <button
              type="button"
              onClick={() => remove(tag)}
              className="ml-1 hover:text-info/80"
              aria-label={`remove ${tag}`}
            >
              ×
            </button>
          </span>
        ))}
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 100)}
          onKeyDown={e => {
            if (e.key === 'Enter' && input.trim()) {
              e.preventDefault();
              add(input.trim());
            }
          }}
          placeholder={selected.length === 0 ? 'Filter by tags...' : ''}
          className="min-w-[6rem] flex-1 bg-transparent px-1 text-sm outline-none"
          data-testid="tag-input"
        />
      </div>
      {open && suggestions.length > 0 && (
        <ul className="absolute left-0 right-0 z-10 mt-1 max-h-48 overflow-y-auto rounded border border-border bg-bg shadow-lg">
          {suggestions.map(tag => (
            <li
              key={tag}
              onMouseDown={() => add(tag)}
              className="cursor-pointer px-2 py-1 text-sm hover:bg-bg-subtle"
              data-testid={`tag-suggestion-${tag}`}
            >
              {tag}
              {CANONICAL_TAGS.includes(tag) && (
                <span className="ml-2 text-xs text-text-tertiary">canonical</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
