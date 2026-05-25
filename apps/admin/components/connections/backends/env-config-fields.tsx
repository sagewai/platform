// Copyright 2026 Ali Arda Diri, Berlin, Germany
// AGPL-3.0-or-later
'use client';

type Props = {
  config: Record<string, unknown>;
  setConfig: (c: Record<string, unknown>) => void;
};

export function EnvConfigFields({ config, setConfig }: Props) {
  const fieldToEnv = (config.field_to_env as Record<string, string>) ?? {};
  // Operator types comma-separated path=ENV_VAR pairs; we parse into the map.
  const display = Object.entries(fieldToEnv)
    .map(([k, v]) => `${k}=${v}`).join(', ');

  return (
    <div data-testid="env-config-fields" className="space-y-2 mt-2">
      <label className="block text-sm">
        Field → env var mapping (comma-separated <code>path=NAME</code>)
        <input
          type="text"
          defaultValue={display}
          onBlur={(e) => {
            const parsed: Record<string, string> = {};
            for (const pair of e.target.value.split(',').map(s => s.trim()).filter(Boolean)) {
              const [k, v] = pair.split('=').map(s => s.trim());
              if (k && v) parsed[k] = v;
            }
            setConfig({ field_to_env: parsed });
          }}
          className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
          data-testid="env-field-mapping"
          placeholder="client_secret=SPOTIFY_SECRET, tokens.access_token=SPOTIFY_AT"
        />
      </label>
    </div>
  );
}
