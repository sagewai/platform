'use client';

import { useState } from 'react';
import {
  Button, Dialog, FormField, TextInput, TextArea, Select, useToast,
} from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type {
  CustomAuthShape,
  InferenceProviderCatalogEntry,
  InferenceProviderKey,
  InferenceProviderMetadata,
  InferenceProviderWritePayload,
} from '@/utils/types';

interface Props {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
  catalogEntry: InferenceProviderCatalogEntry;
  current: InferenceProviderMetadata | null;
}

export function InferenceProviderModal({
  open, onClose, onSaved, catalogEntry, current,
}: Props) {
  const provider = catalogEntry.provider;
  const [secrets, setSecrets] = useState<Record<string, string>>(
    Object.fromEntries(catalogEntry.secret_keys.map((k) => [k, ''])),
  );
  const [env, setEnv] = useState<Record<string, string>>(
    Object.fromEntries(catalogEntry.env_keys.map((k) => [k, current?.env?.[k] ?? ''])),
  );
  const [authShape, setAuthShape] = useState<CustomAuthShape>(
    (current?.env?.CUSTOM_AUTH_SHAPE as CustomAuthShape | undefined) ?? 'bearer',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();

  function setSecret(key: string, value: string) {
    setSecrets({ ...secrets, [key]: value });
  }
  function setEnvVal(key: string, value: string) {
    setEnv({ ...env, [key]: value });
  }

  async function onSave() {
    setBusy(true);
    setError(null);
    // Strip empty secrets so the operator can save partial config without
    // overwriting an existing secret with empty-string.
    const filteredSecrets: Record<string, string> = {};
    for (const [k, v] of Object.entries(secrets)) {
      if (v) filteredSecrets[k] = v;
    }
    const filteredEnv: Record<string, string> = {};
    for (const [k, v] of Object.entries(env)) {
      if (v) filteredEnv[k] = v;
    }
    const payload: InferenceProviderWritePayload = {
      secrets: filteredSecrets,
      env: filteredEnv,
    };
    if (provider === 'custom') payload.auth_shape = authShape;
    try {
      await adminApi.upsertInferenceProvider(provider, payload);
      toast(
        'success',
        `${catalogEntry.label} credentials saved (encrypted at rest).`,
      );
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`${current?.configured ? 'Edit' : 'Add'} ${catalogEntry.label} credentials`}
      actions={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={onSave} disabled={busy}>
            {busy ? 'Saving…' : 'Save credentials'}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-xs text-muted-foreground">
          {catalogEntry.tagline}
          {catalogEntry.example && (
            <>
              {' '}Companion example: <code>{catalogEntry.example}.py</code>.
            </>
          )}
        </p>

        {provider === 'custom' && (
          <FormField label="Auth shape" hint="How the endpoint authenticates requests.">
            <Select
              value={authShape}
              onChange={(e) => setAuthShape(e.target.value as CustomAuthShape)}
            >
              <option value="none">none — no auth header</option>
              <option value="bearer">bearer — Authorization: Bearer &lt;value&gt;</option>
              <option value="basic">basic — user:password (HTTP basic)</option>
              <option value="sigv4">
                sigv4 — AWS SigV4 (stored, not v1.0-tested)
              </option>
            </Select>
          </FormField>
        )}

        {catalogEntry.env_keys.map((envKey) => (
          <FormField
            key={envKey}
            label={envKey}
            hint={
              envKey === 'CUSTOM_BASE_URL'
                ? 'Full URL including scheme. e.g. https://my-vllm.acme.com'
                : envKey === 'CUSTOM_MODEL_NAME'
                  ? 'Model identifier the endpoint expects. e.g. qwen2.5-coder:7b'
                  : undefined
            }
          >
            <TextInput
              value={env[envKey] ?? ''}
              onChange={(e) => setEnvVal(envKey, e.target.value)}
              placeholder={envKey}
            />
          </FormField>
        ))}

        {catalogEntry.secret_keys.map((secretKey) => {
          const isMultiline = secretKey === 'GOOGLE_DRIVE_OAUTH_JSON';
          const hint = (() => {
            if (secretKey === 'GOOGLE_DRIVE_OAUTH_JSON') {
              return 'Paste the entire client_secret_*.json downloaded from Google Cloud Console (OAuth Desktop client).';
            }
            if (secretKey === 'CUSTOM_AUTH_VALUE' && authShape === 'basic') {
              return 'Format: user:password';
            }
            if (secretKey === 'CUSTOM_AUTH_VALUE' && authShape === 'bearer') {
              return 'Token sent as `Authorization: Bearer <value>`.';
            }
            if (current?.secret_keys?.includes(secretKey)) {
              return 'Leave blank to keep the existing encrypted value.';
            }
            return undefined;
          })();
          return (
            <FormField key={secretKey} label={secretKey} hint={hint}>
              {isMultiline ? (
                <TextArea
                  value={secrets[secretKey] ?? ''}
                  onChange={(e) => setSecret(secretKey, e.target.value)}
                  placeholder='{"installed":{"client_id":"...","client_secret":"..."}}'
                  rows={8}
                  className="font-mono text-xs"
                />
              ) : (
                <TextInput
                  type="password"
                  value={secrets[secretKey] ?? ''}
                  onChange={(e) => setSecret(secretKey, e.target.value)}
                  placeholder={secretKey}
                  autoComplete="off"
                />
              )}
            </FormField>
          );
        })}

        {error && (
          <div className="text-destructive text-xs" role="alert">
            {error}
          </div>
        )}

        <p className="text-xs text-muted-foreground border-t pt-3">
          Stored encrypted-at-rest via Sealed (Fernet, AES-128-CBC + HMAC).
          Decryption requires the Sealed master key. Project scope:{' '}
          <code>{current?.project_id ?? 'org-global'}</code>.
        </p>
      </div>
    </Dialog>
  );
}

// Helper used by the page when wiring delete confirmations.
export async function deleteProviderCredentials(
  provider: InferenceProviderKey,
): Promise<void> {
  await adminApi.deleteInferenceProvider(provider);
}
