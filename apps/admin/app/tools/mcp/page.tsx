'use client';

import { useEffect, useState } from 'react';
import { adminApi } from '@/utils/api';
import type { McpServer, McpTool, McpCallResponse } from '@/utils/types';
import { Card, Button, Badge, Skeleton, EmptyState } from '@/components/ui/legacy';

export default function McpToolsPage() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Discover panel
  const [discoverCmd, setDiscoverCmd] = useState('');
  const [discoveredTools, setDiscoveredTools] = useState<McpTool[]>([]);
  const [discovering, setDiscovering] = useState(false);

  // Call panel
  const [callServerCmd, setCallServerCmd] = useState('');
  const [callToolName, setCallToolName] = useState('');
  const [callArgs, setCallArgs] = useState('{}');
  const [calling, setCalling] = useState(false);
  const [callResult, setCallResult] = useState<McpCallResponse | null>(null);

  useEffect(() => {
    adminApi
      .listMcpServers()
      .then(setServers)
      .catch(() => setError('Failed to load MCP servers.'))
      .finally(() => setLoading(false));
  }, []);

  async function handleDiscover() {
    if (!discoverCmd) return;
    setDiscovering(true);
    setDiscoveredTools([]);
    try {
      const data = await adminApi.discoverMcpTools(discoverCmd);
      setDiscoveredTools(data.tools);
      setError(null);
    } catch {
      setError('Discovery failed. Is the server command valid?');
    } finally {
      setDiscovering(false);
    }
  }

  async function handleCall() {
    if (!callServerCmd || !callToolName) return;
    setCalling(true);
    setCallResult(null);
    try {
      const args = JSON.parse(callArgs);
      const data = await adminApi.callMcpTool(callServerCmd, callToolName, args);
      setCallResult(data);
      setError(null);
    } catch (e) {
      setError(e instanceof SyntaxError ? 'Invalid JSON in arguments' : 'Tool call failed.');
    } finally {
      setCalling(false);
    }
  }

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="mt-0 mb-2 text-2xl font-bold font-[family-name:var(--font-heading)]">MCP Tool Browser</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Discover and test MCP tool servers.
      </p>

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {/* Server list */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Registered Servers</h3>
        {loading ? (
          <Skeleton lines={3} />
        ) : servers.length === 0 ? (
          <EmptyState title="No Servers" description="No MCP servers found in mcp-servers/ directory." />
        ) : (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border">
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Name</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Path</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Status</th>
              </tr>
            </thead>
            <tbody>
              {servers.map((s) => (
                <tr key={s.name} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
                  <td className="py-2.5 px-3 font-medium">{s.name}</td>
                  <td className="py-2.5 px-3 text-[13px] text-text-secondary font-[family-name:var(--font-mono)]">{s.path}</td>
                  <td className="py-2.5 px-3">
                    <Badge variant="success">{s.status}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Discover tools */}
      <Card className="mb-lg">
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Discover Tools</h3>
        <div className="flex gap-3 mb-md">
          <input
            className="flex-1 px-3 py-2 border border-border rounded-md text-sm bg-bg-surface"
            placeholder="Server command (e.g. python -m mcp_knowledge)"
            value={discoverCmd}
            onChange={(e) => setDiscoverCmd(e.target.value)}
          />
          <Button onClick={handleDiscover} disabled={discovering || !discoverCmd}>
            {discovering ? 'Discovering...' : 'Discover'}
          </Button>
        </div>
        {discoveredTools.length > 0 && (
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b-2 border-border">
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Tool Name</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Description</th>
                <th className="text-left py-2.5 px-3 text-xs text-text-muted uppercase tracking-wide">Parameters</th>
              </tr>
            </thead>
            <tbody>
              {discoveredTools.map((t) => (
                <tr key={t.name} className="border-b border-border last:border-0 hover:bg-bg-subtle transition-colors">
                  <td className="py-2.5 px-3 font-medium font-[family-name:var(--font-mono)]">{t.name}</td>
                  <td className="py-2.5 px-3 text-[13px] text-text-secondary">{t.description}</td>
                  <td className="py-2.5 px-3 text-xs font-[family-name:var(--font-mono)] text-text-muted">
                    {Object.keys(t.parameters).length > 0 ? JSON.stringify(t.parameters, null, 0) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* Call tool */}
      <Card>
        <h3 className="mt-0 mb-md text-base font-semibold font-[family-name:var(--font-heading)]">Test Tool Call</h3>
        <div className="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label className="block text-[13px] text-text-muted mb-1">Server Command</label>
            <input className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface" placeholder="python -m mcp_knowledge" value={callServerCmd} onChange={(e) => setCallServerCmd(e.target.value)} />
          </div>
          <div>
            <label className="block text-[13px] text-text-muted mb-1">Tool Name</label>
            <input className="w-full px-3 py-2 border border-border rounded-md text-sm bg-bg-surface" placeholder="search_knowledge" value={callToolName} onChange={(e) => setCallToolName(e.target.value)} />
          </div>
        </div>
        <div className="mb-3">
          <label className="block text-[13px] text-text-muted mb-1">Arguments (JSON)</label>
          <textarea
            className="w-full px-3 py-2 border border-border rounded-md text-[13px] bg-bg-surface font-[family-name:var(--font-mono)] min-h-[80px]"
            value={callArgs}
            onChange={(e) => setCallArgs(e.target.value)}
          />
        </div>
        <Button onClick={handleCall} disabled={calling || !callServerCmd || !callToolName}>
          {calling ? 'Calling...' : 'Execute'}
        </Button>

        {callResult && (
          <div className="mt-md bg-bg-subtle rounded-md p-4">
            <div className="text-xs text-text-muted mb-2">
              Tool: <strong>{callResult.tool_name}</strong>
            </div>
            <pre className="m-0 text-[13px] whitespace-pre-wrap break-words">
              {JSON.stringify(callResult.result, null, 2)}
            </pre>
          </div>
        )}
      </Card>
    </div>
  );
}
