// Copyright 2026 Ali Arda Diri, Berlin, Germany
//
// This file is part of Sagewai, licensed under the GNU Affero General
// Public License v3.0 or later (AGPL-3.0-or-later).
import { test, expect } from '@playwright/test';

test.describe('Connections page', () => {
  test('filter + add oauth2 + authorize end-to-end', async ({ page }) => {
    // Mock /protocols
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [
          { id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] },
          {
            id: 'oauth2',
            display_name: 'OAuth 2.0',
            sensitive_fields: ['client_secret', 'tokens.access_token', 'tokens.refresh_token'],
          },
          { id: 'mcp', display_name: 'MCP server', sensitive_fields: [] },
          { id: 'inference', display_name: 'Inference provider', sensitive_fields: [] },
          { id: 'sdk', display_name: 'SDK builtin', sensitive_fields: [] },
        ],
      }),
    );

    // Mock /backends
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [
          { id: 'local', display_name: 'Local encrypted file' },
          { id: 'env',   display_name: 'Environment variables' },
          { id: 'sops',  display_name: 'Mozilla SOPS' },
        ],
      }),
    );

    // Mock the unified list endpoint — first GET returns one existing
    // oauth2 connection; subsequent GETs (after add) include the new row too.
    let listCalls = 0;
    await page.route('**/api/v1/admin/connections/**', async (route, req) => {
      const url = req.url();
      const method = req.method();
      // Per-id GET (the wizard's poll). Match the new connection's id and
      // return status=ready so polling resolves quickly.
      if (method === 'GET' && url.includes('/connections/conn_oauth2_new')
          && !url.includes('/start') && !url.includes('/refresh')
          && !url.includes('/revoke') && !url.includes('/test')
          && !url.includes('/set-default')) {
        await route.fulfill({
          json: {
            id: 'conn_oauth2_new', kind: 'connection', protocol: 'oauth2',
            project_id: 'default',
            display_name: 'New Google', tags: ['email'],
            credentials_backend: { kind: 'local', config: {} },
            status: 'ready',
            last_tested_at: null, last_test_ok: null, is_default: false,
            created_at: '2026-05-24T10:00:00+00:00',
            updated_at: '2026-05-24T10:00:00+00:00',
            last_error: null,
            protocol_data: {
              provider: 'google', client_id: 'cid2', client_secret: '***',
              redirect_uri: 'http://localhost/cb',
              requested_scopes: ['openid'], granted_scopes: ['openid'],
              tokens: {
                access_token: '***', refresh_token: '***',
                token_type: 'Bearer',
                expires_at: '2026-05-24T15:00:00+00:00',
                obtained_at: '2026-05-24T10:00:00+00:00',
                last_refreshed_at: null,
              },
            },
          },
        });
        return;
      }
      // oauth2 start endpoint
      if (method === 'POST' && url.includes('/connections/oauth2/') && url.endsWith('/start')) {
        await route.fulfill({
          json: { authorize_url: 'about:blank', state: 'st-test' },
        });
        return;
      }
      // List endpoint: GET /connections/ (with optional ?query)
      if (method === 'GET' && /\/connections\/(\?.*)?$/.test(url)) {
        listCalls++;
        const base = {
          kind: 'connection',
          project_id: 'default',
          credentials_backend: { kind: 'local', config: {} },
          last_tested_at: null,
          last_test_ok: null,
          is_default: true,
          created_at: '2026-05-24T10:00:00+00:00',
          updated_at: '2026-05-24T10:00:00+00:00',
          last_error: null,
        };
        const existing = {
          ...base,
          id: 'conn_oauth2_existing',
          protocol: 'oauth2',
          display_name: 'Existing Spotify',
          tags: ['music'],
          status: 'ready',
          protocol_data: {
            provider: 'spotify', client_id: 'cid', client_secret: '***',
            redirect_uri: 'http://localhost/cb',
            requested_scopes: ['user-read-private'],
            granted_scopes: ['user-read-private'],
            tokens: {
              access_token: '***', refresh_token: '***',
              token_type: 'Bearer',
              expires_at: '2026-05-24T15:00:00+00:00',
              obtained_at: '2026-05-24T10:00:00+00:00',
              last_refreshed_at: null,
            },
          },
        };
        const newly = {
          ...base,
          id: 'conn_oauth2_new', protocol: 'oauth2',
          is_default: false,
          display_name: 'New Google', tags: ['email'],
          status: 'ready',
          protocol_data: {
            provider: 'google', client_id: 'cid2', client_secret: '***',
            redirect_uri: 'http://localhost/cb',
            requested_scopes: ['openid'], granted_scopes: ['openid'],
            tokens: {
              access_token: '***', refresh_token: '***',
              token_type: 'Bearer',
              expires_at: '2026-05-24T15:00:00+00:00',
              obtained_at: '2026-05-24T10:00:00+00:00',
              last_refreshed_at: null,
            },
          },
        };
        const records = listCalls > 1 ? [existing, newly] : [existing];
        await route.fulfill({ json: records });
        return;
      }
      // Create endpoint: POST /connections/
      if (method === 'POST' && /\/connections\/(\?.*)?$/.test(url)) {
        await route.fulfill({
          json: {
            id: 'conn_oauth2_new', kind: 'connection', protocol: 'oauth2',
            project_id: 'default',
            display_name: 'New Google', tags: ['email'],
            credentials_backend: { kind: 'local', config: {} },
            status: 'pending',
            last_tested_at: null, last_test_ok: null, is_default: false,
            created_at: '2026-05-24T10:00:00+00:00',
            updated_at: '2026-05-24T10:00:00+00:00',
            last_error: null,
            protocol_data: {
              provider: 'google', client_id: 'cid2', client_secret: '***',
              redirect_uri: 'http://localhost/cb',
              requested_scopes: ['openid'], granted_scopes: [], tokens: null,
            },
          },
        });
        return;
      }
      await route.continue();
    });

    // Disable popups
    await page.addInitScript(() => { (window as unknown as { open: () => null }).open = () => null; });

    await page.goto('/connections');

    // Filter bar present + existing row visible
    await expect(page.getByTestId('filter-bar')).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId('protocol-chip-oauth2')).toBeVisible();
    await expect(page.getByText('Existing Spotify')).toBeVisible();

    // Click oauth2 chip to filter
    await page.getByTestId('protocol-chip-oauth2').click();
    await expect(page.getByText('Existing Spotify')).toBeVisible();

    // Open Add modal
    await page.getByTestId('add-connection-btn').click();
    await expect(page.getByTestId('add-connection-modal')).toBeVisible();

    // Step 1: pick oauth2
    await page.getByTestId('protocol-pick-oauth2').click();

    // Step 2: fill fields
    await page.getByTestId('display-name-input').fill('New Google');
    await page.getByTestId('oauth-provider-select').selectOption('google');
    await page.getByTestId('client-id-input').fill('cid2');
    await page.getByTestId('client-secret-input').fill('secret2');
    // Scope to step-2 to avoid colliding with Next.js Dev Tools "Next" button.
    await page.getByTestId('step-2').getByRole('button', { name: 'Next' }).click();

    // Step 3: backend + tags
    await page.getByTestId('tags-input').fill('email');
    await page.getByTestId('submit-add-connection').click();

    // Step 'authorizing' shows
    await expect(page.getByTestId('step-authorizing')).toBeVisible({ timeout: 5000 });

    // Modal closes after polling finds status=ready
    await expect(page.getByTestId('add-connection-modal')).not.toBeVisible({ timeout: 15_000 });

    // New row visible after reload
    await expect(page.getByText('New Google')).toBeVisible({ timeout: 5000 });
  });
});
