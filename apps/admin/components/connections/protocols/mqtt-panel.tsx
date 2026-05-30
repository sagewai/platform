// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { useCallback, useEffect, useState } from 'react';
import type {
  Connection,
  MqttProtocolData,
  MqttSubscription,
} from '@/utils/connection-types';
import { adminApi } from '@/utils/api';

type Props = {
  connection: Connection;
  onRefresh?: () => void | Promise<void>;
};

export function MqttPanel({ connection }: Props) {
  const pd = connection.protocol_data as Partial<MqttProtocolData>;

  const [subs, setSubs] = useState<MqttSubscription[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [topicFilter, setTopicFilter] = useState('');
  const [qos, setQos] = useState(0);
  const [drainOutput, setDrainOutput] = useState<string | null>(null);

  const loadSubs = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const rows = await adminApi.connections.mqtt.listSubscriptions();
      // Only show subscriptions bound to THIS connection.
      setSubs(rows.filter(s => s.connection_id === connection.id));
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [connection.id]);

  // Re-sync when the drawer switches to a different connection. Keyed on
  // connection.id only (PR3 lesson: keying on protocol_data re-fires on
  // every parent re-render that produces a fresh reference).
  useEffect(() => {
    setTopicFilter('');
    setQos(0);
    setDrainOutput(null);
    setErr(null);
    void loadSubs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connection.id]);

  async function addSubscription() {
    if (!topicFilter.trim()) return;
    setLoading(true);
    setErr(null);
    try {
      await adminApi.connections.mqtt.subscribe(connection.id, {
        topic_filter: topicFilter.trim(),
        qos,
      });
      setTopicFilter('');
      setQos(0);
      await loadSubs();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function unsubscribe(subId: string) {
    setLoading(true);
    setErr(null);
    try {
      await adminApi.connections.mqtt.unsubscribe(subId);
      await loadSubs();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function drainDebug(subId: string) {
    setLoading(true);
    setErr(null);
    try {
      const dr = await adminApi.connections.mqtt.drain(subId, 50);
      setDrainOutput(JSON.stringify(dr, null, 2));
      await loadSubs();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  const host = pd.host ?? '';
  const port = pd.port ?? 1883;
  const transport = pd.transport ?? 'tcp';
  const mqttVersion = pd.mqtt_version ?? '5.0';
  const username = pd.username ?? '';
  const password = pd.password ?? '';
  const keepalive = pd.keepalive_seconds ?? 60;
  const tierOverride = pd.sandbox_tier_override ?? null;

  return (
    <div className="space-y-6" data-testid="mqtt-panel">
      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">Broker</h3>
        <dl className="mt-2 grid grid-cols-3 gap-2 text-sm">
          <dt className="text-text-tertiary">host</dt>
          <dd className="col-span-2 font-mono">
            {host || <em className="text-text-tertiary">not set</em>}
          </dd>
          <dt className="text-text-tertiary">port</dt>
          <dd className="col-span-2 font-mono">{port}</dd>
          <dt className="text-text-tertiary">transport</dt>
          <dd className="col-span-2 font-mono">{transport}</dd>
          <dt className="text-text-tertiary">mqtt_version</dt>
          <dd className="col-span-2 font-mono">{mqttVersion}</dd>
          <dt className="text-text-tertiary">username</dt>
          <dd className="col-span-2 font-mono">
            {username || <em className="text-text-tertiary">none</em>}
          </dd>
          <dt className="text-text-tertiary">password</dt>
          <dd className="col-span-2 font-mono">
            {password === '***'
              ? '*** (encrypted)'
              : <em className="text-text-tertiary">none</em>}
          </dd>
          <dt className="text-text-tertiary">keepalive</dt>
          <dd className="col-span-2">{keepalive}s</dd>
          <dt className="text-text-tertiary">sandbox tier</dt>
          <dd className="col-span-2">{tierOverride ?? 'SANDBOXED (default)'}</dd>
        </dl>
      </div>

      <div>
        <h3 className="text-sm font-semibold uppercase text-text-secondary">
          Subscriptions
        </h3>
        <p className="mt-1 text-xs text-text-tertiary">
          MQTT is a streaming connection. A subscription buffers inbound messages
          in a fixed-size ring (overflow policy <code>drop_oldest</code> — PR2
          ships drop_oldest only). Drain pulls buffered events; the drop counters
          tell you whether you are seeing the complete stream.
        </p>

        {err && (
          <p
            className="mt-2 rounded bg-error/10 px-3 py-2 text-xs text-error"
            data-testid="mqtt-subs-error"
          >
            {err}
          </p>
        )}

        <table className="mt-3 w-full text-sm" data-testid="mqtt-subs-table">
          <thead>
            <tr className="text-left text-text-tertiary">
              <th className="py-1">Subscription</th>
              <th className="py-1">Status</th>
              <th className="py-1">Buffered</th>
              <th className="py-1">Dropped</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {subs.length === 0 && (
              <tr>
                <td colSpan={5} className="py-2 text-xs italic text-text-tertiary">
                  {loading ? 'Loading…' : 'No active subscriptions.'}
                </td>
              </tr>
            )}
            {subs.map(s => (
              <tr key={s.subscription_id} className="border-t border-border">
                <td className="font-mono text-xs py-1">{s.subscription_id}</td>
                <td className="py-1">{s.status}</td>
                <td className="py-1">{s.buffer_depth}</td>
                <td className="py-1">{s.overflow_dropped}</td>
                <td className="py-1 text-right space-x-2">
                  <button
                    type="button"
                    onClick={() => drainDebug(s.subscription_id)}
                    disabled={loading}
                    className="text-xs text-accent hover:underline disabled:opacity-50"
                    data-testid={`mqtt-drain-${s.subscription_id}`}
                  >
                    Drain
                  </button>
                  <button
                    type="button"
                    onClick={() => unsubscribe(s.subscription_id)}
                    disabled={loading}
                    className="text-xs text-error hover:underline disabled:opacity-50"
                    data-testid={`mqtt-unsubscribe-${s.subscription_id}`}
                  >
                    Unsubscribe
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <div className="mt-3 flex items-end gap-2">
          <div className="flex-1">
            <label className="block text-xs text-text-tertiary">topic_filter</label>
            <input
              type="text"
              placeholder="fleet/+/telemetry"
              value={topicFilter}
              onChange={e => setTopicFilter(e.target.value)}
              data-testid="mqtt-new-topic-filter"
              className="mt-1 w-full rounded border border-border bg-bg px-2 py-1 font-mono text-xs"
            />
          </div>
          <div>
            <label className="block text-xs text-text-tertiary">qos</label>
            <select
              value={qos}
              onChange={e => setQos(Number(e.target.value))}
              data-testid="mqtt-new-qos"
              className="mt-1 rounded border border-border bg-bg px-2 py-1 text-xs"
            >
              <option value={0}>0</option>
              <option value={1}>1</option>
              <option value={2}>2</option>
            </select>
          </div>
          <button
            type="button"
            onClick={addSubscription}
            disabled={loading || !topicFilter.trim()}
            className="rounded bg-accent px-3 py-1 text-xs text-text-on-accent disabled:opacity-50"
            data-testid="mqtt-add-subscription-button"
          >
            Subscribe
          </button>
        </div>

        {drainOutput !== null && (
          <pre
            className="mt-3 max-h-48 overflow-auto rounded bg-bg-secondary px-3 py-2 text-xs font-mono"
            data-testid="mqtt-drain-output"
          >
            {drainOutput}
          </pre>
        )}
      </div>
    </div>
  );
}
