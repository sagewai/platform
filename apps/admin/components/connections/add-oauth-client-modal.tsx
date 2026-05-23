'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button,
  Dialog,
  FormField,
  TextInput,
  useToast,
} from '@/components/ui/legacy';
import { adminApi } from '@/utils/api';
import type {
  OAuthClient,
  OAuthProviderMeta,
} from '@/utils/types';
import { CheckCircle2, ExternalLink, Copy, AlertCircle, Loader2 } from 'lucide-react';

type Step = 'pick' | 'credentials' | 'authorize';

const POLL_INTERVAL_MS = 2000;
const POLL_TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes

export function AddOAuthClientModal({
  onClose,
  onAuthorized,
}: {
  onClose: () => void;
  onAuthorized: () => void;
}) {
  const [step, setStep] = useState<Step>('pick');
  const [providers, setProviders] = useState<OAuthProviderMeta[]>([]);
  const [loadingProviders, setLoadingProviders] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [providerId, setProviderId] = useState<string>('');
  const [displayName, setDisplayName] = useState<string>('');
  const [clientId, setClientId] = useState<string>('');
  const [clientSecret, setClientSecret] = useState<string>('');
  const [scopesText, setScopesText] = useState<string>('');
  const [submitting, setSubmitting] = useState(false);
  const [recordId, setRecordId] = useState<string | null>(null);
  const [authorizeUrl, setAuthorizeUrl] = useState<string | null>(null);
  const [pollState, setPollState] = useState<
    'idle' | 'polling' | 'authorized' | 'error' | 'timeout'
  >('idle');
  const [pollError, setPollError] = useState<string | null>(null);
  const [grantedScopes, setGrantedScopes] = useState<string[]>([]);
  const { toast } = useToast();

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollDeadlineRef = useRef<number | null>(null);

  const selectedProvider = useMemo(
    () => providers.find((p) => p.id === providerId) ?? null,
    [providers, providerId],
  );

  // Load providers on mount.
  useEffect(() => {
    let cancelled = false;
    setLoadingProviders(true);
    adminApi.oauthClients
      .providers()
      .then((list) => {
        if (cancelled) return;
        setProviders(list);
        if (list.length > 0) {
          setProviderId(list[0].id);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoadingProviders(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // When provider changes (step 1) seed display_name + scopes.
  useEffect(() => {
    if (!selectedProvider) return;
    setDisplayName(selectedProvider.display_name);
    setScopesText(selectedProvider.default_scopes.join(' '));
  }, [selectedProvider]);

  // Cleanup poller on unmount.
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, []);

  const redirectUri =
    typeof window !== 'undefined'
      ? `${window.location.origin}/api/v1/admin/connections/oauth/callback`
      : '';

  function copyRedirect() {
    if (!redirectUri) return;
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      navigator.clipboard.writeText(redirectUri).then(
        () => toast('success', 'Redirect URI copied to clipboard'),
        () => toast('error', 'Could not copy — select the URL manually'),
      );
    }
  }

  function startPolling(id: string) {
    pollDeadlineRef.current = Date.now() + POLL_TIMEOUT_MS;
    setPollState('polling');

    const tick = async () => {
      try {
        const record = await adminApi.oauthClients.get(id);
        if (record.status === 'authorized') {
          setGrantedScopes(record.granted_scopes);
          setPollState('authorized');
          toast(
            'success',
            `${record.display_name} connected with ${record.granted_scopes.length} scope${record.granted_scopes.length === 1 ? '' : 's'} granted.`,
          );
          onAuthorized();
          return;
        }
        if (record.status === 'error') {
          setPollError(record.last_error?.message ?? 'Authorization failed.');
          setPollState('error');
          return;
        }
        if (record.status === 'revoked') {
          setPollError('Authorization was revoked.');
          setPollState('error');
          return;
        }
        // pending — keep polling unless deadline passed.
        if (
          pollDeadlineRef.current !== null &&
          Date.now() > pollDeadlineRef.current
        ) {
          setPollState('timeout');
          return;
        }
        pollTimerRef.current = setTimeout(tick, POLL_INTERVAL_MS);
      } catch (e) {
        setPollError(e instanceof Error ? e.message : String(e));
        setPollState('error');
      }
    };
    pollTimerRef.current = setTimeout(tick, POLL_INTERVAL_MS);
  }

  async function handleAuthorize() {
    if (!selectedProvider) return;
    setSubmitting(true);
    setError(null);
    try {
      const scopes = scopesText
        .split(/[\s,]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      const resp = await adminApi.oauthClients.create({
        provider: selectedProvider.id,
        display_name: displayName || selectedProvider.display_name,
        client_id: clientId,
        client_secret: clientSecret,
        requested_scopes: scopes,
      });
      setRecordId(resp.record.id);
      setAuthorizeUrl(resp.authorize_url);
      setStep('authorize');
      // Pop the consent window.
      if (typeof window !== 'undefined') {
        window.open(resp.authorize_url, '_blank', 'width=600,height=800');
      }
      startPolling(resp.record.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTryAgain() {
    if (!recordId) return;
    setPollError(null);
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    try {
      const resp = await adminApi.oauthClients.start(recordId);
      setAuthorizeUrl(resp.authorize_url);
      if (typeof window !== 'undefined') {
        window.open(resp.authorize_url, '_blank', 'width=600,height=800');
      }
      startPolling(recordId);
    } catch (e) {
      setPollError(e instanceof Error ? e.message : String(e));
      setPollState('error');
    }
  }

  // ── Render: actions vary by step ──────────────────────────────────────────
  function renderActions() {
    if (step === 'pick') {
      return (
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => setStep('credentials')}
            disabled={!selectedProvider}
            data-testid="oauth-next-button"
          >
            Next
          </Button>
        </>
      );
    }
    if (step === 'credentials') {
      return (
        <>
          <Button
            variant="ghost"
            onClick={() => setStep('pick')}
            disabled={submitting}
          >
            Back
          </Button>
          <Button
            onClick={handleAuthorize}
            disabled={!clientId || !clientSecret || submitting}
            data-testid="oauth-save-button"
          >
            {submitting ? 'Saving…' : 'Save and Authorize'}
          </Button>
        </>
      );
    }
    // authorize step
    return (
      <Button onClick={onClose} variant="ghost">
        {pollState === 'authorized' ? 'Close' : 'Cancel'}
      </Button>
    );
  }

  // ── Render: body varies by step ───────────────────────────────────────────
  function renderBody() {
    if (step === 'pick') {
      return (
        <div className="space-y-4">
          {loadingProviders && (
            <p className="text-xs text-muted-foreground">Loading providers…</p>
          )}

          {!loadingProviders && providers.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No OAuth providers available.
            </p>
          )}

          {!loadingProviders && providers.length > 0 && (
            <>
              <FormField label="Provider" hint="Choose the OAuth provider to connect.">
                <select
                  data-testid="oauth-provider-select"
                  className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                  value={providerId}
                  onChange={(e) => setProviderId(e.target.value)}
                >
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.display_name}
                    </option>
                  ))}
                </select>
              </FormField>

              {selectedProvider && (
                <>
                  <div className="rounded-md border border-border p-3 text-xs space-y-2">
                    <div>
                      <span className="text-muted-foreground">Provider docs: </span>
                      <a
                        href={selectedProvider.docs_url}
                        target="_blank"
                        rel="noreferrer"
                        className="underline inline-flex items-center gap-1"
                      >
                        {selectedProvider.docs_url}
                        <ExternalLink className="w-3 h-3" />
                      </a>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Default scopes: </span>
                      <code className="text-[11px] font-mono">
                        {selectedProvider.default_scopes.join(' ')}
                      </code>
                    </div>
                  </div>

                  <div className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/30 p-3 text-xs">
                    <div className="font-semibold mb-1">
                      Register this Redirect URI in your {selectedProvider.display_name} console
                    </div>
                    <p className="text-muted-foreground mb-2">
                      Add the URL below to the allowed redirect/callback list before
                      continuing.
                    </p>
                    <div className="flex items-center gap-2">
                      <code
                        data-testid="oauth-redirect-uri"
                        className="flex-1 font-mono text-[11px] break-all px-2 py-1.5 rounded bg-background border border-border"
                      >
                        {redirectUri}
                      </code>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={copyRedirect}
                        data-testid="oauth-redirect-copy"
                      >
                        <Copy className="w-3.5 h-3.5 mr-1.5" />
                        Copy
                      </Button>
                    </div>
                  </div>
                </>
              )}
            </>
          )}

          {error && (
            <div className="text-destructive text-xs" role="alert">
              {error}
            </div>
          )}
        </div>
      );
    }

    if (step === 'credentials') {
      return (
        <div className="space-y-4">
          <FormField label="Display name" hint="Shown in the OAuth tab table.">
            <TextInput
              name="display_name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder={selectedProvider?.display_name}
              autoComplete="off"
            />
          </FormField>

          <FormField label="Client ID" hint="Copy from your provider console.">
            <TextInput
              name="client_id"
              type="password"
              value={clientId}
              onChange={(e) => setClientId(e.target.value)}
              placeholder="Client ID"
              autoComplete="off"
            />
          </FormField>

          <FormField label="Client secret" hint="Stored encrypted-at-rest via Sealed.">
            <TextInput
              name="client_secret"
              type="password"
              value={clientSecret}
              onChange={(e) => setClientSecret(e.target.value)}
              placeholder="Client secret"
              autoComplete="off"
            />
          </FormField>

          <FormField
            label="Requested scopes"
            hint="Space or comma separated. Pre-filled with the provider defaults."
          >
            <TextInput
              name="requested_scopes"
              value={scopesText}
              onChange={(e) => setScopesText(e.target.value)}
              placeholder={selectedProvider?.default_scopes.join(' ')}
              autoComplete="off"
            />
          </FormField>

          {error && (
            <div className="text-destructive text-xs" role="alert">
              {error}
            </div>
          )}

          <p className="text-xs text-muted-foreground border-t pt-3">
            Credentials are stored encrypted-at-rest via Sealed (Fernet,
            AES-128-CBC + HMAC). Tokens never leave the vault — only metadata
            (expires_at, granted_scopes) is exposed to the UI.
          </p>
        </div>
      );
    }

    // step === 'authorize'
    return (
      <div className="space-y-4" data-testid="oauth-authorize-step">
        {pollState === 'polling' && (
          <div className="flex items-start gap-3">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground mt-0.5 shrink-0" />
            <div className="text-sm">
              <p className="font-medium">
                Authorizing {selectedProvider?.display_name}…
              </p>
              <p className="text-muted-foreground text-xs mt-1">
                A new browser window has opened to complete the OAuth dance.
                This window will update automatically once the provider
                redirects back. If the popup was blocked,{' '}
                <a
                  href={authorizeUrl ?? '#'}
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  open it manually
                </a>
                .
              </p>
            </div>
          </div>
        )}

        {pollState === 'authorized' && (
          <div className="flex items-start gap-3">
            <CheckCircle2 className="w-5 h-5 text-green-600 mt-0.5 shrink-0" />
            <div className="text-sm">
              <p className="font-medium">
                {selectedProvider?.display_name} connected.
              </p>
              <p className="text-muted-foreground text-xs mt-1">
                {grantedScopes.length} scope{grantedScopes.length === 1 ? '' : 's'} granted:{' '}
                <code className="text-[11px] font-mono">
                  {grantedScopes.join(' ')}
                </code>
              </p>
            </div>
          </div>
        )}

        {pollState === 'error' && (
          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-destructive mt-0.5 shrink-0" />
              <div className="text-sm">
                <p className="font-medium text-destructive">
                  Authorization failed
                </p>
                <p className="text-muted-foreground text-xs mt-1">
                  {pollError ?? 'Unknown error.'}
                </p>
              </div>
            </div>
            <Button size="sm" onClick={handleTryAgain}>
              Try again
            </Button>
          </div>
        )}

        {pollState === 'timeout' && (
          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-amber-600 mt-0.5 shrink-0" />
              <div className="text-sm">
                <p className="font-medium">Authorization didn&rsquo;t complete</p>
                <p className="text-muted-foreground text-xs mt-1">
                  The browser flow didn&rsquo;t return within 10 minutes. The
                  client is still registered — you can retry below or finish
                  the authorization later from the row&rsquo;s action menu.
                </p>
              </div>
            </div>
            <Button size="sm" onClick={handleTryAgain}>
              Try again
            </Button>
          </div>
        )}
      </div>
    );
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title={
        step === 'pick'
          ? 'Add OAuth client — provider'
          : step === 'credentials'
            ? `Add OAuth client — credentials`
            : `Add OAuth client — authorize`
      }
      actions={renderActions()}
    >
      {renderBody()}
    </Dialog>
  );
}
