'use client';

import { Cpu, Shield, Server } from 'lucide-react';

interface PlaceholderSlotProps {
  icon: React.ReactNode;
  label: string;
}

function PlaceholderSlot({ icon, label }: PlaceholderSlotProps) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 bg-bg-subtle border border-border border-dashed rounded-lg opacity-60">
      <span className="shrink-0 text-text-muted">{icon}</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-text-secondary m-0">{label}</p>
        <p className="text-xs text-text-muted m-0 mt-0.5 italic">Pending</p>
      </div>
      <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide bg-bg-surface border border-border text-text-muted px-2 py-0.5 rounded">
        v1.2
      </span>
    </div>
  );
}

export function AutopilotResourceAllocation() {
  return (
    <div className="space-y-3">
      <p className="text-sm text-text-secondary m-0">
        Resource allocation panel — workers, sandbox tiers, and sealed credential bindings
        appear here once composition retrieval is wired in v1.2.
      </p>
      <div className="space-y-2">
        <PlaceholderSlot
          icon={<Server size={15} />}
          label="Workers"
        />
        <PlaceholderSlot
          icon={<Cpu size={15} />}
          label="Sandbox"
        />
        <PlaceholderSlot
          icon={<Shield size={15} />}
          label="Credentials"
        />
      </div>
    </div>
  );
}
