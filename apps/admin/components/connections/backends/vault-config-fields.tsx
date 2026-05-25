// Copyright 2026 Ali Arda Diri, Berlin, Germany
// AGPL-3.0-or-later
'use client';

type Props = {
  config: Record<string, unknown>;
  setConfig: (c: Record<string, unknown>) => void;
};

export function VaultConfigFields({ config, setConfig }: Props) {
  const authMode = (config.auth as { mode?: string })?.mode ?? 'token';

  const setField = (k: string, v: unknown) => setConfig({ ...config, [k]: v });
  const setAuth = (auth: Record<string, unknown>) => setConfig({ ...config, auth });

  return (
    <div data-testid="vault-config-fields" className="space-y-2 mt-2">
      <label className="block text-sm">
        Vault URL
        <input type="text"
               defaultValue={(config.url as string) ?? ''}
               onBlur={(e) => setField('url', e.target.value)}
               className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
               data-testid="vault-url" placeholder="https://vault.example.com:8200" />
      </label>
      <label className="block text-sm">
        Mount path (KV v2)
        <input type="text"
               defaultValue={(config.mount as string) ?? 'secret'}
               onBlur={(e) => setField('mount', e.target.value)}
               className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
               data-testid="vault-mount" placeholder="secret" />
      </label>
      <label className="block text-sm">
        Namespace (optional; HCP Cloud / Enterprise)
        <input type="text"
               defaultValue={(config.namespace as string) ?? ''}
               onBlur={(e) => setField('namespace', e.target.value || null)}
               className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
               data-testid="vault-namespace" placeholder="admin/team-a" />
      </label>
      <label className="block text-sm">
        Base path (per-connection; secrets live at <code>&lt;mount&gt;/data/&lt;base_path&gt;</code>)
        <input type="text"
               defaultValue={(config.base_path as string) ?? ''}
               onBlur={(e) => setField('base_path', e.target.value)}
               className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
               data-testid="vault-base-path" placeholder="sagewai/spotify-marketing" />
      </label>
      <label className="block text-sm">
        Auth mode
        <select defaultValue={authMode}
                onChange={(e) => {
                  const newMode = e.target.value;
                  if (newMode === 'token') {
                    setAuth({ mode: 'token', token: '' });
                  } else {
                    setAuth({ mode: 'approle', role_id: '', secret_id: '' });
                  }
                }}
                className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm"
                data-testid="vault-auth-mode">
          <option value="token">Token (dev)</option>
          <option value="approle">AppRole (production)</option>
        </select>
      </label>
      {authMode === 'token' ? (
        <label className="block text-sm">
          Vault token
          <input type="password"
                 defaultValue={((config.auth as Record<string, unknown>)?.token as string) ?? ''}
                 onBlur={(e) => setAuth({ mode: 'token', token: e.target.value })}
                 className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
                 data-testid="vault-token" placeholder="hvs.CAES..." />
        </label>
      ) : (
        <>
          <label className="block text-sm">
            AppRole role_id
            <input type="text"
                   defaultValue={((config.auth as Record<string, unknown>)?.role_id as string) ?? ''}
                   onBlur={(e) => setAuth({
                     mode: 'approle',
                     role_id: e.target.value,
                     secret_id: ((config.auth as Record<string, unknown>)?.secret_id as string) ?? '',
                   })}
                   className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
                   data-testid="vault-role-id" />
          </label>
          <label className="block text-sm">
            AppRole secret_id
            <input type="password"
                   defaultValue={((config.auth as Record<string, unknown>)?.secret_id as string) ?? ''}
                   onBlur={(e) => setAuth({
                     mode: 'approle',
                     role_id: ((config.auth as Record<string, unknown>)?.role_id as string) ?? '',
                     secret_id: e.target.value,
                   })}
                   className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
                   data-testid="vault-secret-id" />
          </label>
        </>
      )}
      <label className="flex items-center gap-2 text-sm mt-1">
        <input type="checkbox"
               defaultChecked={(config.verify_tls as boolean) !== false}
               onChange={(e) => setField('verify_tls', e.target.checked)}
               data-testid="vault-verify-tls" />
        Verify TLS (uncheck only for self-signed dev clusters)
      </label>
    </div>
  );
}
