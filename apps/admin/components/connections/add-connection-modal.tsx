// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type {
  BackendMeta,
  Connection,
  CreateConnectionPayload,
  CredentialsBackendKind,
  McpServerMeta,
  ProtocolMeta,
} from '@/utils/connection-types';

type Props = {
  open: boolean;
  protocols: ProtocolMeta[];
  backends: BackendMeta[];
  defaultBackend: CredentialsBackendKind;
  onClose: () => void;
  onAuthorized: (connection: Connection) => void;
};

type Step = 1 | 2 | 3 | 'authorizing' | 'done' | 'error';

export function AddConnectionModal({
  open, protocols, backends, defaultBackend, onClose, onAuthorized,
}: Props) {
  const [step, setStep] = useState<Step>(1);
  const [protocol, setProtocol] = useState<ProtocolMeta | null>(null);
  const [displayName, setDisplayName] = useState('');
  const [tags, setTags] = useState<string[]>([]);
  const [protocolData, setProtocolData] = useState<Record<string, unknown>>({});
  const [backendKind, setBackendKind] = useState<CredentialsBackendKind>(defaultBackend);
  const [backendConfig, setBackendConfig] = useState<Record<string, unknown>>({});
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Reset on open
  useEffect(() => {
    if (open) {
      setStep(1);
      setProtocol(null);
      setDisplayName('');
      setTags([]);
      setProtocolData({});
      setBackendKind(defaultBackend);
      setBackendConfig({});
      setCreatedId(null);
      setErrorMsg(null);
    }
  }, [open, defaultBackend]);

  // Poll for oauth2 authorization completion
  useEffect(() => {
    if (step !== 'authorizing' || !createdId) return;
    const deadline = Date.now() + 10 * 60 * 1000;
    const interval = setInterval(async () => {
      if (Date.now() > deadline) {
        clearInterval(interval);
        setErrorMsg("Authorization didn't complete in 10 minutes.");
        setStep('error');
        return;
      }
      try {
        const fresh = await adminApi.connections.get(createdId);
        if (fresh.status === 'ready') {
          clearInterval(interval);
          setStep('done');
          onAuthorized(fresh);
        } else if (fresh.status === 'error') {
          clearInterval(interval);
          setErrorMsg(fresh.last_error?.message ?? 'unknown error');
          setStep('error');
        }
      } catch (_e) { /* keep polling */ }
    }, 2000);
    return () => clearInterval(interval);
  }, [step, createdId, onAuthorized]);

  if (!open) return null;

  const handleSubmit = async () => {
    if (!protocol) return;
    const payload: CreateConnectionPayload = {
      protocol: protocol.id,
      display_name: displayName,
      tags,
      credentials_backend: { kind: backendKind, config: backendConfig },
      protocol_data: protocolData,
    };
    try {
      const created = await adminApi.connections.create(payload);
      setCreatedId(created.id);
      if (protocol.id === 'oauth2') {
        const startRes = await adminApi.connections.oauth2.start(created.id);
        window.open(startRes.authorize_url, '_blank', 'width=600,height=800');
        setStep('authorizing');
      } else {
        setStep('done');
        onAuthorized(created);
      }
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
      setStep('error');
    }
  };

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/30"
      data-testid="add-connection-modal"
    >
      <div className="max-h-[90vh] w-[36rem] overflow-y-auto rounded-lg bg-bg shadow-xl">
        <header className="flex items-center justify-between border-b border-border p-4">
          <h2 className="text-lg font-semibold">Add Connection</h2>
          <button
            onClick={onClose}
            aria-label="close"
            className="text-text-tertiary hover:text-text-primary"
          >
            ✕
          </button>
        </header>
        <div className="p-4">
          {step === 1 && (
            <Step1PickProtocol
              protocols={protocols}
              selected={protocol}
              onSelect={p => { setProtocol(p); setStep(2); }}
            />
          )}
          {step === 2 && protocol && (
            <Step2Configure
              protocol={protocol}
              displayName={displayName}
              setDisplayName={setDisplayName}
              protocolData={protocolData}
              setProtocolData={setProtocolData}
              onNext={() => setStep(3)}
              onBack={() => setStep(1)}
            />
          )}
          {step === 3 && protocol && (
            <Step3BackendAndTags
              backends={backends}
              backendKind={backendKind}
              setBackendKind={setBackendKind}
              backendConfig={backendConfig}
              setBackendConfig={setBackendConfig}
              tags={tags}
              setTags={setTags}
              onBack={() => setStep(2)}
              onSubmit={handleSubmit}
            />
          )}
          {step === 'authorizing' && (
            <p className="text-sm text-text-primary" data-testid="step-authorizing">
              Waiting for authorization. Approve the consent screen in the popup; this modal closes when {protocol?.display_name} reports back.
            </p>
          )}
          {step === 'done' && (
            <p className="text-sm text-success" data-testid="step-done">
              Connection created.
            </p>
          )}
          {step === 'error' && (
            <div className="text-sm text-error" data-testid="step-error">
              <p>Error: {errorMsg}</p>
              <Button
                onClick={() => setStep(protocol?.id === 'oauth2' ? 3 : 2)}
                className="mt-2"
              >
                Try again
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Step1PickProtocol({
  protocols, selected, onSelect,
}: {
  protocols: ProtocolMeta[];
  selected: ProtocolMeta | null;
  onSelect: (p: ProtocolMeta) => void;
}) {
  return (
    <div className="grid grid-cols-2 gap-2" data-testid="step-1">
      {protocols.map(p => (
        <button
          key={p.id}
          type="button"
          onClick={() => onSelect(p)}
          className={`rounded border p-3 text-left hover:border-accent ${
            selected?.id === p.id
              ? 'border-accent bg-accent/10'
              : 'border-border'
          }`}
          data-testid={`protocol-pick-${p.id}`}
        >
          <p className="font-semibold">{p.display_name}</p>
          <p className="mt-1 text-xs text-text-tertiary">{p.id}</p>
        </button>
      ))}
    </div>
  );
}

function Step2Configure(props: {
  protocol: ProtocolMeta;
  displayName: string;
  setDisplayName: (s: string) => void;
  protocolData: Record<string, unknown>;
  setProtocolData: (d: Record<string, unknown>) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  return (
    <div data-testid="step-2">
      <label className="mb-3 block">
        <span className="text-sm font-medium">Display name</span>
        <input
          type="text"
          value={props.displayName}
          onChange={e => props.setDisplayName(e.target.value)}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          data-testid="display-name-input"
        />
      </label>
      {props.protocol.id === 'oauth2' && (
        <Oauth2ConfigureFields data={props.protocolData} setData={props.setProtocolData} />
      )}
      {props.protocol.id === 'http' && (
        <HttpConfigureFields data={props.protocolData} setData={props.setProtocolData} />
      )}
      {props.protocol.id === 'mcp' && (
        <McpConfigureFields data={props.protocolData} setData={props.setProtocolData} />
      )}
      {props.protocol.id === 'inference' && (
        <InferenceConfigureFields data={props.protocolData} setData={props.setProtocolData} />
      )}
      {props.protocol.id === 'sdk' && (
        <SdkConfigureFields data={props.protocolData} setData={props.setProtocolData} />
      )}
      <div className="mt-4 flex justify-between">
        <Button onClick={props.onBack} variant="secondary">Back</Button>
        <Button onClick={props.onNext}>Next</Button>
      </div>
    </div>
  );
}

function Oauth2ConfigureFields({
  data, setData,
}: {
  data: Record<string, unknown>;
  setData: (d: Record<string, unknown>) => void;
}) {
  return (
    <>
      <label className="mb-2 block">
        <span className="text-sm">Provider</span>
        <select
          value={(data.provider as string) ?? 'spotify'}
          onChange={e => setData({ ...data, provider: e.target.value })}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          data-testid="oauth-provider-select"
        >
          <option value="spotify">Spotify</option>
          <option value="google">Google</option>
        </select>
      </label>
      <label className="mb-2 block">
        <span className="text-sm">Client ID</span>
        <input
          type="text"
          onChange={e => setData({ ...data, client_id: e.target.value })}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 font-mono text-sm"
          data-testid="client-id-input"
        />
      </label>
      <label className="mb-2 block">
        <span className="text-sm">Client Secret</span>
        <input
          type="password"
          onChange={e => setData({ ...data, client_secret: e.target.value })}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 font-mono text-sm"
          data-testid="client-secret-input"
        />
      </label>
    </>
  );
}

function HttpConfigureFields({
  data, setData,
}: {
  data: Record<string, unknown>;
  setData: (d: Record<string, unknown>) => void;
}) {
  return (
    <>
      <label className="mb-2 block">
        <span className="text-sm">Base URL</span>
        <input
          type="url"
          onChange={e => setData({ ...data, base_url: e.target.value })}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 font-mono text-sm"
          data-testid="base-url-input"
          placeholder="https://api.example.com"
        />
      </label>
    </>
  );
}

function McpConfigureFields({
  data, setData,
}: {
  data: Record<string, unknown>;
  setData: (d: Record<string, unknown>) => void;
}) {
  const [servers, setServers] = useState<McpServerMeta[]>([]);
  const [picked, setPicked] = useState<McpServerMeta | null>(null);

  useEffect(() => {
    adminApi.connections.mcp
      .servers()
      .then(setServers)
      .catch(() => setServers([]));
  }, []);

  const handlePick = (id: string) => {
    if (id === '__custom__') {
      setPicked(null);
      setData({ transport: 'stdio', command: [] });
      return;
    }
    const entry = servers.find(s => s.id === id);
    if (!entry) return;
    setPicked(entry);
    setData({
      server_ref: entry.id,
      transport: entry.transport,
      command: entry.default_command ?? [],
      args: entry.default_args ?? [],
      credentials: Object.fromEntries(
        entry.credential_fields.map(f => [f.name, '']),
      ),
    });
  };

  const updateCred = (name: string, value: string) => {
    const credentials = {
      ...((data.credentials as Record<string, string> | undefined) ?? {}),
      [name]: value,
    };
    setData({ ...data, credentials });
  };

  const transport = (data.transport as string) ?? 'stdio';

  return (
    <>
      <label className="mb-2 block">
        <span className="text-sm">Server</span>
        <select
          onChange={e => handlePick(e.target.value)}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          data-testid="mcp-server-select"
        >
          <option value="__custom__">Custom (free-form)</option>
          {servers.map(s => (
            <option key={s.id} value={s.id}>
              {s.display_name} — {s.description}
            </option>
          ))}
        </select>
      </label>

      {picked ? (
        <>
          <div className="mb-2 text-xs text-text-tertiary">
            <span className="font-mono">
              {(picked.default_command ?? []).join(' ')}
            </span>
          </div>
          {(picked.id === 'filesystem' || picked.id === 'sqlite') && (
            <label className="mb-2 block">
              <span className="text-sm">
                {picked.id === 'filesystem'
                  ? 'Root path'
                  : 'Database file path'}
              </span>
              <input
                type="text"
                onChange={e =>
                  setData({
                    ...data,
                    args: [
                      ...(picked.default_args ?? []),
                      e.target.value,
                    ],
                  })
                }
                className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 font-mono text-sm"
                data-testid="mcp-path-arg-input"
              />
            </label>
          )}
          {picked.credential_fields.map(f => (
            <label key={f.name} className="mb-2 block">
              <span className="text-sm">{f.label}</span>
              <input
                type={f.type === 'password' ? 'password' : 'text'}
                onChange={e => updateCred(f.name, e.target.value)}
                className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 font-mono text-sm"
                data-testid={`mcp-cred-${f.name}`}
              />
              {f.description && (
                <p className="mt-0.5 text-xs text-text-tertiary">
                  {f.description}
                </p>
              )}
            </label>
          ))}
        </>
      ) : (
        <>
          <label className="mb-2 block">
            <span className="text-sm">Transport</span>
            <select
              value={transport}
              onChange={e => setData({ ...data, transport: e.target.value })}
              className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 text-sm"
              data-testid="mcp-transport-select"
            >
              <option value="stdio">stdio</option>
              <option value="http">http</option>
              <option value="sse">sse</option>
            </select>
          </label>
          {transport === 'stdio' ? (
            <label className="mb-2 block">
              <span className="text-sm">Command (space-separated)</span>
              <input
                type="text"
                onChange={e =>
                  setData({
                    ...data,
                    command: e.target.value.split(/\s+/).filter(Boolean),
                  })
                }
                className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 font-mono text-sm"
                data-testid="mcp-command-input"
              />
            </label>
          ) : (
            <label className="mb-2 block">
              <span className="text-sm">URL</span>
              <input
                type="url"
                onChange={e => setData({ ...data, url: e.target.value })}
                className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 font-mono text-sm"
                data-testid="mcp-url-input"
              />
            </label>
          )}
        </>
      )}
    </>
  );
}

function InferenceConfigureFields({
  data, setData,
}: {
  data: Record<string, unknown>;
  setData: (d: Record<string, unknown>) => void;
}) {
  return (
    <>
      <label className="mb-2 block">
        <span className="text-sm">Provider key</span>
        <select
          value={(data.provider_key as string) ?? 'runpod'}
          onChange={e => setData({ ...data, provider_key: e.target.value })}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          data-testid="inference-provider-select"
        >
          <option value="runpod">RunPod</option>
          <option value="modal">Modal</option>
          <option value="vastai">Vast.ai</option>
          <option value="colab">Colab</option>
          <option value="custom">Custom</option>
        </select>
      </label>
      <label className="mb-2 block">
        <span className="text-sm">Base URL (optional)</span>
        <input
          type="url"
          onChange={e => setData({ ...data, base_url: e.target.value })}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 font-mono text-sm"
          data-testid="inference-base-url-input"
        />
      </label>
    </>
  );
}

function SdkConfigureFields({
  data, setData,
}: {
  data: Record<string, unknown>;
  setData: (d: Record<string, unknown>) => void;
}) {
  return (
    <label className="mb-2 block">
      <span className="text-sm">Entrypoint (dotted path)</span>
      <input
        type="text"
        onChange={e => setData({
          ...data,
          entrypoint: e.target.value,
          credential_fields: [],
        })}
        className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 font-mono text-sm"
        data-testid="sdk-entrypoint-input"
        placeholder="sagewai.tools.executors.sdk:paypal_api"
      />
    </label>
  );
}

function Step3BackendAndTags(props: {
  backends: BackendMeta[];
  backendKind: CredentialsBackendKind;
  setBackendKind: (k: CredentialsBackendKind) => void;
  backendConfig: Record<string, unknown>;
  setBackendConfig: (c: Record<string, unknown>) => void;
  tags: string[];
  setTags: (t: string[]) => void;
  onBack: () => void;
  onSubmit: () => void;
}) {
  return (
    <div data-testid="step-3">
      <label className="mb-3 block">
        <span className="text-sm font-medium">Credentials backend</span>
        <select
          value={props.backendKind}
          onChange={e => props.setBackendKind(e.target.value as CredentialsBackendKind)}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          data-testid="backend-select"
        >
          {props.backends.map(b => (
            <option key={b.id} value={b.id}>{b.display_name}</option>
          ))}
        </select>
      </label>
      <label className="mb-3 block">
        <span className="text-sm font-medium">Tags (comma-separated)</span>
        <input
          type="text"
          value={props.tags.join(', ')}
          onChange={e => props.setTags(
            e.target.value.split(',').map(s => s.trim()).filter(Boolean),
          )}
          className="mt-1 block w-full rounded border border-border bg-bg px-2 py-1 text-sm"
          data-testid="tags-input"
        />
      </label>
      <div className="mt-4 flex justify-between">
        <Button onClick={props.onBack} variant="secondary">Back</Button>
        <Button onClick={props.onSubmit} data-testid="submit-add-connection">
          Create
        </Button>
      </div>
    </div>
  );
}
