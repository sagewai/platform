// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import type { Connection, ModbusProtocolData } from '@/utils/connection-types';

const BUILTIN_OPS: Array<{ name: string; args: string; returns: string }> = [
  { name: 'read_coils',               args: 'address, count, unit_id?',          returns: 'list[bool]' },
  { name: 'read_discrete_inputs',     args: 'address, count, unit_id?',          returns: 'list[bool]' },
  { name: 'read_holding_registers',   args: 'address, count, unit_id?',          returns: 'list[int]' },
  { name: 'read_input_registers',     args: 'address, count, unit_id?',          returns: 'list[int]' },
  { name: 'write_single_coil',        args: 'address, value: bool, unit_id?',    returns: '{ok: true}' },
  { name: 'write_single_register',    args: 'address, value: int, unit_id?',     returns: '{ok: true}' },
  { name: 'write_multiple_coils',     args: 'address, values: list[bool], unit_id?', returns: '{ok: true}' },
  { name: 'write_multiple_registers', args: 'address, values: list[int], unit_id?',  returns: '{ok: true}' },
];

export function ModbusPanel({ connection }: { connection: Connection }) {
  const pd = connection.protocol_data as Partial<ModbusProtocolData>;
  const host = pd.host ?? '';
  const port = pd.port ?? 502;
  const transport = pd.transport ?? 'tcp';
  const unitId = pd.unit_id ?? 1;
  const timeout = pd.default_timeout_seconds ?? 3;
  const tierOverride = pd.sandbox_tier_override ?? null;

  return (
    <div className="space-y-6" data-testid="modbus-panel">
      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">Endpoint</h3>
        <dl className="mt-2 grid grid-cols-3 gap-2 text-sm">
          <dt className="text-text-tertiary">host</dt>
          <dd className="col-span-2 font-mono">
            {host || <em className="text-text-tertiary">not set</em>}
          </dd>
          <dt className="text-text-tertiary">port</dt>
          <dd className="col-span-2 font-mono">{port}</dd>
          <dt className="text-text-tertiary">transport</dt>
          <dd className="col-span-2 font-mono">{transport}</dd>
          <dt className="text-text-tertiary">unit_id</dt>
          <dd className="col-span-2 font-mono">{unitId}</dd>
          <dt className="text-text-tertiary">timeout</dt>
          <dd className="col-span-2">{timeout}s</dd>
          <dt className="text-text-tertiary">sandbox tier</dt>
          <dd className="col-span-2">{tierOverride ?? 'UNTRUSTED (default)'}</dd>
        </dl>
        <p className="mt-3 rounded bg-warning/10 px-3 py-2 text-xs text-warning">
          Modbus/TCP has no authentication. Firewall/VPN-gate the device to trusted networks only.
        </p>
      </div>

      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">
          Operations (builtin)
        </h3>
        <p className="mt-1 text-xs text-text-tertiary">
          Modbus exposes eight fixed function codes. Operators do not declare custom operations.
        </p>
        <table className="mt-2 w-full text-sm" data-testid="modbus-builtin-ops">
          <thead>
            <tr className="text-left text-text-tertiary">
              <th className="py-1">Op</th>
              <th className="py-1">Args</th>
              <th className="py-1">Returns</th>
            </tr>
          </thead>
          <tbody>
            {BUILTIN_OPS.map(op => (
              <tr key={op.name} className="border-t border-border">
                <td className="font-mono py-1 text-xs">{op.name}</td>
                <td className="font-mono text-xs py-1">{op.args}</td>
                <td className="font-mono text-xs py-1">{op.returns}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
