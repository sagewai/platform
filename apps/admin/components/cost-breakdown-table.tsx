'use client';

import { useState } from 'react';
import { Card, EmptyState } from '@/components/ui/legacy';

interface CostBreakdownEntry {
  name: string;
  cost: number;
  tokens: number;
  requests: number;
}

interface CostBreakdownTableProps {
  title: string;
  data: CostBreakdownEntry[];
}

type SortKey = 'name' | 'cost' | 'tokens' | 'requests';

export function CostBreakdownTable({ title, data }: CostBreakdownTableProps) {
  const [sortKey, setSortKey] = useState<SortKey>('cost');
  const [sortDesc, setSortDesc] = useState(true);

  const sorted = [...data].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    if (typeof av === 'string' && typeof bv === 'string') {
      return sortDesc ? bv.localeCompare(av) : av.localeCompare(bv);
    }
    return sortDesc ? (bv as number) - (av as number) : (av as number) - (bv as number);
  });

  function handleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDesc(!sortDesc);
    } else {
      setSortKey(key);
      setSortDesc(true);
    }
  }

  const indicator = (key: SortKey) => (sortKey === key ? (sortDesc ? ' v' : ' ^') : '');

  if (data.length === 0) {
    return (
      <Card>
        <h3 className="mt-0 mb-3 text-base font-semibold font-[family-name:var(--font-heading)]">{title}</h3>
        <EmptyState title="No Data" description="No data available." />
      </Card>
    );
  }

  return (
    <Card>
      <h3 className="mt-0 mb-3 text-base font-semibold font-[family-name:var(--font-heading)]">{title}</h3>
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b-2 border-border">
            <th
              className={`text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide cursor-pointer select-none ${sortKey === 'name' ? 'bg-primary-light' : ''}`}
              onClick={() => handleSort('name')}
            >
              Name{indicator('name')}
            </th>
            <th
              className={`text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide cursor-pointer select-none ${sortKey === 'cost' ? 'bg-primary-light' : ''}`}
              onClick={() => handleSort('cost')}
            >
              Cost (USD){indicator('cost')}
            </th>
            <th
              className={`text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide cursor-pointer select-none ${sortKey === 'tokens' ? 'bg-primary-light' : ''}`}
              onClick={() => handleSort('tokens')}
            >
              Tokens{indicator('tokens')}
            </th>
            <th
              className={`text-right py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide cursor-pointer select-none ${sortKey === 'requests' ? 'bg-primary-light' : ''}`}
              onClick={() => handleSort('requests')}
            >
              Requests{indicator('requests')}
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((entry) => (
            <tr key={entry.name} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
              <td className="py-2.5 px-3">{entry.name}</td>
              <td className="py-2.5 px-3 text-right">${entry.cost.toFixed(4)}</td>
              <td className="py-2.5 px-3 text-right">{entry.tokens.toLocaleString()}</td>
              <td className="py-2.5 px-3 text-right">{entry.requests.toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
