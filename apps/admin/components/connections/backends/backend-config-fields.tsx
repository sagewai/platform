// Copyright 2026 Ali Arda Diri, Berlin, Germany
// AGPL-3.0-or-later
'use client';

import type { CredentialsBackendKind } from '@/utils/connection-types';
import { DopplerConfigFields } from './doppler-config-fields';
import { EnvConfigFields } from './env-config-fields';
import { LocalConfigFields } from './local-config-fields';
import { SopsConfigFields } from './sops-config-fields';
import { VaultConfigFields } from './vault-config-fields';

type Props = {
  backend: CredentialsBackendKind;
  config: Record<string, unknown>;
  setConfig: (c: Record<string, unknown>) => void;
};

export function BackendConfigFields({ backend, config, setConfig }: Props) {
  switch (backend) {
    case 'local':   return <LocalConfigFields />;
    case 'env':     return <EnvConfigFields config={config} setConfig={setConfig} />;
    case 'sops':    return <SopsConfigFields config={config} setConfig={setConfig} />;
    case 'vault':   return <VaultConfigFields config={config} setConfig={setConfig} />;
    case 'doppler': return <DopplerConfigFields config={config} setConfig={setConfig} />;
    default:        return null;
  }
}
