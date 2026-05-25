// Copyright 2026 Ali Arda Diri, Berlin, Germany
// AGPL-3.0-or-later
'use client';

type Props = {
  config: Record<string, unknown>;
  setConfig: (c: Record<string, unknown>) => void;
};

export function DopplerConfigFields({ config, setConfig }: Props) {
  const setField = (k: string, v: unknown) => setConfig({ ...config, [k]: v });
  return (
    <div data-testid="doppler-config-fields" className="space-y-2 mt-2">
      <label className="block text-sm">
        Service token (from Doppler dashboard → Access)
        <input type="password"
               defaultValue={(config.service_token as string) ?? ''}
               onBlur={(e) => setField('service_token', e.target.value)}
               className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
               data-testid="doppler-service-token" placeholder="dp.st.dev.XXXX..." />
      </label>
      <label className="block text-sm">
        Project (Doppler project name)
        <input type="text"
               defaultValue={(config.project as string) ?? ''}
               onBlur={(e) => setField('project', e.target.value)}
               className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm"
               data-testid="doppler-project" placeholder="sagewai" />
      </label>
      <label className="block text-sm">
        Config (Doppler environment, e.g., &quot;prd&quot; or &quot;dev&quot;)
        <input type="text"
               defaultValue={(config.config as string) ?? ''}
               onBlur={(e) => setField('config', e.target.value)}
               className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm"
               data-testid="doppler-config" placeholder="prd" />
      </label>
      <label className="block text-sm">
        Name prefix (UPPER_SNAKE; secret names auto-derive as <code>&lt;prefix&gt;_&lt;FIELD_PATH&gt;</code>)
        <input type="text"
               defaultValue={(config.name_prefix as string) ?? ''}
               onBlur={(e) => setField('name_prefix', e.target.value.toUpperCase())}
               className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
               data-testid="doppler-name-prefix" placeholder="SPOTIFY_MARKETING" />
      </label>
      <label className="block text-sm">
        Base URL (optional — for Doppler Enterprise self-host)
        <input type="text"
               defaultValue={(config.base_url as string) ?? ''}
               onBlur={(e) => setField('base_url', e.target.value || undefined)}
               className="mt-1 block w-full border border-gray-300 rounded px-2 py-1 text-sm font-mono"
               data-testid="doppler-base-url" placeholder="https://api.doppler.com (default)" />
      </label>
    </div>
  );
}
