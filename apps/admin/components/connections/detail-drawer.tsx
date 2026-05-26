// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { JSX } from 'react';
import type { Connection } from '@/utils/connection-types';
import { BackendPill } from './backend-pill';
import { StatusPill } from './status-pill';
import { CoapPanel } from './protocols/coap-panel';
import { HttpPanel } from './protocols/http-panel';
import { InferencePanel } from './protocols/inference-panel';
import { McpPanel } from './protocols/mcp-panel';
import { ModbusPanel } from './protocols/modbus-panel';
import { Oauth2Panel } from './protocols/oauth2-panel';
import { OpcuaPanel } from './protocols/opcua-panel';
import { SdkPanel } from './protocols/sdk-panel';

type PanelProps = {
  connection: Connection;
  onRefresh?: () => void | Promise<void>;
};

const PANELS: Record<string, (props: PanelProps) => JSX.Element> = {
  http: HttpPanel,
  oauth2: Oauth2Panel,
  mcp: McpPanel,
  inference: InferencePanel,
  sdk: SdkPanel,
  coap: CoapPanel,
  modbus: ModbusPanel,
  opcua: OpcuaPanel,
};

type Props = {
  connection: Connection | null;
  protocolNames: Record<string, string>;
  onClose: () => void;
  /** Triggered by plugin-specific actions (e.g. McpPanel "Refresh tools"). */
  onRefresh?: () => void | Promise<void>;
};

export function DetailDrawer({ connection, protocolNames, onClose, onRefresh }: Props) {
  if (!connection) return null;
  const Panel = PANELS[connection.protocol];
  return (
    <>
      <div
        className="fixed inset-0 z-20 bg-black/20"
        onClick={onClose}
        data-testid="drawer-backdrop"
      />
      <aside
        className="fixed bottom-0 right-0 top-0 z-30 w-96 overflow-y-auto border-l border-border bg-bg shadow-xl"
        data-testid="detail-drawer"
      >
        <header className="sticky top-0 border-b border-border bg-bg p-4">
          <div className="flex items-start justify-between">
            <div>
              <p className="text-xs uppercase text-text-tertiary">
                {protocolNames[connection.protocol] ?? connection.protocol}
              </p>
              <h2 className="text-lg font-semibold">{connection.display_name}</h2>
              <p className="mt-0.5 font-mono text-xs text-text-tertiary">
                {connection.id}
              </p>
            </div>
            <button
              onClick={onClose}
              aria-label="close"
              className="px-2 text-text-tertiary hover:text-text-primary"
            >
              ✕
            </button>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <StatusPill status={connection.status} />
            <BackendPill kind={connection.credentials_backend?.kind ?? null} />
            {connection.tags.map(t => (
              <span
                key={t}
                className="rounded bg-bg-subtle px-1.5 py-0.5 text-xs text-text-secondary"
              >
                {t}
              </span>
            ))}
          </div>
        </header>
        <div className="p-4">
          {Panel ? <Panel connection={connection} onRefresh={onRefresh} /> : (
            <p className="text-sm text-text-tertiary">
              No detail panel registered for protocol {connection.protocol}.
            </p>
          )}
        </div>
        {connection.last_error && (
          <div className="m-4 rounded border border-error/30 bg-error/10 p-4 text-xs">
            <p className="font-semibold text-error">
              Last error: {connection.last_error.code}
            </p>
            <p className="mt-1 text-error">{connection.last_error.message}</p>
            <p className="mt-1 text-text-tertiary">{connection.last_error.at}</p>
          </div>
        )}
      </aside>
    </>
  );
}
