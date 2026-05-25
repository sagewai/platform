// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { Button } from '@/components/ui/legacy';
import type { ProtocolMeta } from '@/utils/connection-types';
import { ProtocolChip } from './protocol-chip';
import { TagMultiSelect } from './tag-multi-select';

type Props = {
  protocols: ProtocolMeta[];
  selectedProtocol: string | null; // null = All
  onSelectProtocol: (id: string | null) => void;
  selectedTags: string[];
  knownTags: string[];
  onChangeTags: (tags: string[]) => void;
  search: string;
  onChangeSearch: (s: string) => void;
  onAddConnection: () => void;
};

export function FilterBar({
  protocols, selectedProtocol, onSelectProtocol,
  selectedTags, knownTags, onChangeTags,
  search, onChangeSearch, onAddConnection,
}: Props) {
  return (
    <div className="mb-4 flex flex-col gap-3" data-testid="filter-bar">
      <div className="flex flex-wrap items-center gap-2">
        <ProtocolChip
          id="__all"
          label="All"
          selected={selectedProtocol === null}
          onClick={() => onSelectProtocol(null)}
        />
        {protocols.map(p => (
          <ProtocolChip
            key={p.id}
            id={p.id}
            label={p.display_name}
            selected={selectedProtocol === p.id}
            onClick={() => onSelectProtocol(p.id)}
          />
        ))}
      </div>
      <div className="flex items-center gap-2">
        <div className="flex-1">
          <TagMultiSelect
            selected={selectedTags}
            knownTags={knownTags}
            onChange={onChangeTags}
          />
        </div>
        <input
          type="search"
          value={search}
          onChange={e => onChangeSearch(e.target.value)}
          placeholder="Search by name, id, tag..."
          className="w-64 rounded border border-border bg-bg px-3 py-1.5 text-sm"
          data-testid="connections-search"
        />
        <Button onClick={onAddConnection} data-testid="add-connection-btn">
          + Add Connection
        </Button>
      </div>
    </div>
  );
}
