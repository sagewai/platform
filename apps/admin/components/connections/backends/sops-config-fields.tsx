// Copyright 2026 Ali Arda Diri, Berlin, Germany
// AGPL-3.0-or-later
'use client';

type Props = {
  config: Record<string, unknown>;
  setConfig: (c: Record<string, unknown>) => void;
};

export function SopsConfigFields({ config, setConfig }: Props) {
  return (
    <div data-testid="sops-config-fields" className="space-y-2 mt-2">
      <label className="block text-sm">
        SOPS file path (relative to <code>SAGEWAI_SOPS_ROOT</code>)
        <input
          type="text"
          defaultValue={(config.file as string) ?? ''}
          onBlur={(e) => setConfig({ ...config, file: e.target.value })}
          className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
          data-testid="sops-file"
          placeholder="secrets/spotify-marketing.sops.yaml"
        />
      </label>
      <label className="block text-sm">
        Top-level key inside the decrypted YAML
        <input
          type="text"
          defaultValue={(config.key as string) ?? ''}
          onBlur={(e) => setConfig({ ...config, key: e.target.value })}
          className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
          data-testid="sops-key"
          placeholder="client_secret"
        />
      </label>
      <label className="block text-sm">
        Optional dotted key path (multi-field secrets)
        <input
          type="text"
          defaultValue={(config.key_path as string) ?? ''}
          onBlur={(e) => setConfig({ ...config, key_path: e.target.value })}
          className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
          data-testid="sops-key-path"
          placeholder="tokens.access_token"
        />
      </label>
    </div>
  );
}
