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

  test('add connection with vault backend', async ({ page }) => {
    // Register the broad catch-all FIRST so the more-specific protocols/backends
    // routes (registered after) take precedence — Playwright tries routes LIFO.
    let createdBody: Record<string, unknown> | null = null;
    await page.route('**/api/v1/admin/connections/**', async (route, req) => {
      const url = req.url();
      const method = req.method();
      if (method === 'POST' && /\/connections\/(\?.*)?$/.test(url)) {
        createdBody = JSON.parse(req.postData() ?? '{}');
        await route.fulfill({
          json: {
            id: 'conn_test', kind: 'connection', protocol: 'http',
            project_id: 'default',
            display_name: 'Test Vault', tags: [],
            credentials_backend: { kind: 'vault', config: {} },
            status: 'ready',
            last_tested_at: null, last_test_ok: null, is_default: false,
            created_at: '2026-05-25T00:00:00+00:00',
            updated_at: '2026-05-25T00:00:00+00:00',
            last_error: null, protocol_data: {},
          },
        });
        return;
      }
      // List, get, etc. — return empty
      await route.fulfill({ json: [] });
    });
    // Specific routes registered LAST so they win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [{ id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] }],
      }));
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [
          { id: 'local', display_name: 'Local encrypted file' },
          { id: 'vault', display_name: 'HashiCorp Vault' },
        ],
      }));

    await page.goto('/connections');
    await page.getByTestId('add-connection-btn').click();
    await page.getByTestId('protocol-pick-http').click();
    await page.getByTestId('display-name-input').fill('Test Vault');
    await page.getByTestId('base-url-input').fill('https://api.example.com');
    await page.getByTestId('step-2').getByRole('button', { name: 'Next' }).click();
    // Step 3: pick vault backend
    await page.getByTestId('backend-select').selectOption('vault');
    await page.getByTestId('vault-url').fill('https://vault.example.com:8200');
    await page.getByTestId('vault-base-path').fill('sagewai/test');
    await page.getByTestId('vault-token').fill('hvs.stub');
    await page.getByTestId('submit-add-connection').click();

    // Modal closes after successful create
    await expect(page.getByTestId('add-connection-modal')).not.toBeVisible({ timeout: 10_000 });

    // Verify the create payload had the right shape
    expect(createdBody).not.toBeNull();
    const body = createdBody as unknown as { credentials_backend: { kind: string; config: Record<string, unknown> } };
    expect(body.credentials_backend.kind).toBe('vault');
    expect(body.credentials_backend.config.url).toBe('https://vault.example.com:8200');
    expect(body.credentials_backend.config.base_path).toBe('sagewai/test');
    expect((body.credentials_backend.config.auth as { mode: string }).mode).toBe('token');
  });


  test('coap: add → test (mocked) → delete', async ({ page }) => {
    let connections: Array<Record<string, unknown>> = [];
    let createdBody: Record<string, unknown> | null = null;

    // Broad catch-all FIRST so specific routes registered LATER win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/**', async (route, req) => {
      const url = req.url();
      const method = req.method();
      // Per-id GET
      const idMatch = url.match(/\/connections\/(conn_coap_[a-z0-9]+)$/);
      if (method === 'GET' && idMatch) {
        const found = connections.find(c => c.id === idMatch[1]);
        if (found) {
          await route.fulfill({ json: found });
        } else {
          await route.fulfill({ status: 404, json: {} });
        }
        return;
      }
      // Per-id DELETE (catch-all — narrower handler added later wins).
      if (method === 'DELETE' && idMatch) {
        connections = connections.filter(c => c.id !== idMatch[1]);
        await route.fulfill({ json: null, status: 200 });
        return;
      }
      // Per-id /test (POST)
      const testMatch = url.match(/\/connections\/(conn_coap_[a-z0-9]+)\/test$/);
      if (method === 'POST' && testMatch) {
        await route.fulfill({
          json: { ok: true, status_code: null, message: 'coap discovery returned 2.05' },
        });
        return;
      }
      // List endpoint
      if (method === 'GET' && /\/connections\/(\?.*)?$/.test(url)) {
        await route.fulfill({ json: connections });
        return;
      }
      // Create endpoint
      if (method === 'POST' && /\/connections\/(\?.*)?$/.test(url)) {
        createdBody = JSON.parse(req.postData() ?? '{}');
        const created = {
          id: 'conn_coap_abc123',
          kind: 'connection',
          protocol: 'coap',
          project_id: 'default',
          display_name: 'e2e-coap-thermostat',
          tags: ['iot'],
          credentials_backend: { kind: 'local', config: {} },
          status: 'ready',
          last_tested_at: null,
          last_test_ok: null,
          is_default: true,
          created_at: '2026-05-25T00:00:00+00:00',
          updated_at: '2026-05-25T00:00:00+00:00',
          last_error: null,
          protocol_data: {
            base_uri: 'coap://thermostat.example.com:5683',
            use_dtls: false,
            psk_identity: '',
            psk_key: '',
            default_timeout_seconds: 10,
            sandbox_tier_override: null,
          },
        };
        connections.push(created);
        await route.fulfill({ json: created });
        return;
      }
      await route.continue();
    });

    // Specific routes registered LAST so they win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [
          { id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] },
          { id: 'coap', display_name: 'CoAP', sensitive_fields: ['psk_key'] },
        ],
      }));
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [{ id: 'local', display_name: 'Local encrypted file' }],
      }));

    await page.goto('/connections');
    await expect(page.getByTestId('filter-bar')).toBeVisible({ timeout: 10_000 });

    // Open Add modal
    await page.getByTestId('add-connection-btn').click();
    await expect(page.getByTestId('add-connection-modal')).toBeVisible();

    // Step 1: pick CoAP
    await page.getByTestId('protocol-pick-coap').click();

    // Step 2: fill the form
    await page.getByTestId('display-name-input').fill('e2e-coap-thermostat');
    await page.getByTestId('coap-base-uri').fill('coap://thermostat.example.com:5683');
    await page.getByTestId('step-2').getByRole('button', { name: 'Next' }).click();

    // Step 3: defaults are fine
    await page.getByTestId('tags-input').fill('iot');
    await page.getByTestId('submit-add-connection').click();

    // Modal closes
    await expect(page.getByTestId('add-connection-modal')).not.toBeVisible({ timeout: 10_000 });

    // New row visible
    await expect(page.getByText('e2e-coap-thermostat')).toBeVisible();

    // Verify the create payload shape
    expect(createdBody).not.toBeNull();
    const body = createdBody as unknown as { protocol: string; protocol_data: Record<string, unknown> };
    expect(body.protocol).toBe('coap');
    expect(body.protocol_data.base_uri).toBe('coap://thermostat.example.com:5683');
    expect(body.protocol_data.use_dtls).toBe(false);

    // ── Test action ────────────────────────────────────────────────
    // Track POST /test invocations so we can assert it ran.
    let testCalls = 0;
    await page.route(
      '**/api/v1/admin/connections/conn_coap_abc123/test',
      async (route) => {
        testCalls += 1;
        await route.fulfill({
          json: {
            ok: true,
            status_code: null,
            message: 'coap discovery returned 2.05',
          },
        });
      },
    );

    // Open the row's actions menu (the ⋯ details) and click Test.
    const row = page.getByTestId('connection-row-conn_coap_abc123');
    await row.getByLabel('row actions').click();
    await row.getByRole('button', { name: 'Test' }).click();
    // The Test action triggers a reload(); wait for the toast to settle.
    await expect.poll(() => testCalls).toBeGreaterThan(0);

    // ── Delete action ──────────────────────────────────────────────
    let deleteCalls = 0;
    await page.route(
      '**/api/v1/admin/connections/conn_coap_abc123',
      async (route, req) => {
        if (req.method() === 'DELETE') {
          deleteCalls += 1;
          connections = connections.filter(c => c.id !== 'conn_coap_abc123');
          // The fetch client's .json() chokes on a truly-empty 204 body, so
          // return null-JSON instead (real server replies are funnelled
          // through the same code path during e2e).
          await route.fulfill({ json: null, status: 200 });
          return;
        }
        await route.continue();
      },
    );

    // The Delete handler calls window.confirm(); auto-accept it.
    page.once('dialog', d => d.accept());
    // Re-open the actions menu (it auto-closed after Test) and click Delete.
    await row.getByLabel('row actions').click();
    await row.getByRole('button', { name: 'Delete' }).click();
    await expect.poll(() => deleteCalls).toBeGreaterThan(0);
    // Row should disappear after the reload (use the row-id testid to avoid
    // toast-text false matches on the display name).
    await expect(
      page.getByTestId('connection-row-conn_coap_abc123'),
    ).not.toBeVisible({ timeout: 10_000 });
  });


  test('modbus: add → test (mocked) → delete', async ({ page }) => {
    let connections: Array<Record<string, unknown>> = [];
    let createdBody: Record<string, unknown> | null = null;

    // Broad catch-all FIRST so specific routes registered LATER win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/**', async (route, req) => {
      const url = req.url();
      const method = req.method();
      // Per-id GET
      const idMatch = url.match(/\/connections\/(conn_modbus_[a-z0-9]+)$/);
      if (method === 'GET' && idMatch) {
        const found = connections.find(c => c.id === idMatch[1]);
        if (found) {
          await route.fulfill({ json: found });
        } else {
          await route.fulfill({ status: 404, json: {} });
        }
        return;
      }
      // Per-id DELETE (catch-all — narrower handler added later wins).
      if (method === 'DELETE' && idMatch) {
        connections = connections.filter(c => c.id !== idMatch[1]);
        await route.fulfill({ json: null, status: 200 });
        return;
      }
      // Per-id /test (POST)
      const testMatch = url.match(/\/connections\/(conn_modbus_[a-z0-9]+)\/test$/);
      if (method === 'POST' && testMatch) {
        await route.fulfill({
          json: { ok: true, status_code: null, message: 'modbus connection ok' },
        });
        return;
      }
      // List endpoint
      if (method === 'GET' && /\/connections\/(\?.*)?$/.test(url)) {
        await route.fulfill({ json: connections });
        return;
      }
      // Create endpoint
      if (method === 'POST' && /\/connections\/(\?.*)?$/.test(url)) {
        createdBody = JSON.parse(req.postData() ?? '{}');
        const created = {
          id: 'conn_modbus_xyz789',
          kind: 'connection',
          protocol: 'modbus',
          project_id: 'default',
          display_name: 'e2e-modbus-pump',
          tags: ['industrial'],
          credentials_backend: { kind: 'local', config: {} },
          status: 'ready',
          last_tested_at: null,
          last_test_ok: null,
          is_default: true,
          created_at: '2026-05-25T00:00:00+00:00',
          updated_at: '2026-05-25T00:00:00+00:00',
          last_error: null,
          protocol_data: {
            host: '192.168.1.50',
            port: 502,
            transport: 'tcp',
            unit_id: 2,
            default_timeout_seconds: 3,
            sandbox_tier_override: null,
          },
        };
        connections.push(created);
        await route.fulfill({ json: created });
        return;
      }
      await route.continue();
    });

    // Specific routes registered LAST so they win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [
          { id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] },
          { id: 'modbus', display_name: 'Modbus', sensitive_fields: [] },
        ],
      }));
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [{ id: 'local', display_name: 'Local encrypted file' }],
      }));

    await page.goto('/connections');
    await expect(page.getByTestId('filter-bar')).toBeVisible({ timeout: 10_000 });

    // Open Add modal
    await page.getByTestId('add-connection-btn').click();
    await expect(page.getByTestId('add-connection-modal')).toBeVisible();

    // Step 1: pick Modbus
    await page.getByTestId('protocol-pick-modbus').click();

    // Step 2: fill the form
    await page.getByTestId('display-name-input').fill('e2e-modbus-pump');
    await page.getByTestId('modbus-host').fill('192.168.1.50');
    await page.getByTestId('modbus-unit-id').fill('2');
    await page.getByTestId('step-2').getByRole('button', { name: 'Next' }).click();

    // Step 3: defaults are fine
    await page.getByTestId('tags-input').fill('industrial');
    await page.getByTestId('submit-add-connection').click();

    // Modal closes
    await expect(page.getByTestId('add-connection-modal')).not.toBeVisible({ timeout: 10_000 });

    // New row visible
    await expect(page.getByText('e2e-modbus-pump')).toBeVisible();

    // Verify the create payload shape
    expect(createdBody).not.toBeNull();
    const body = createdBody as unknown as { protocol: string; protocol_data: Record<string, unknown> };
    expect(body.protocol).toBe('modbus');
    expect(body.protocol_data.host).toBe('192.168.1.50');
    expect(body.protocol_data.unit_id).toBe(2);
    expect(body.protocol_data.transport).toBe('tcp');

    // ── Test action ────────────────────────────────────────────────
    let testCalls = 0;
    await page.route(
      '**/api/v1/admin/connections/conn_modbus_xyz789/test',
      async (route) => {
        testCalls += 1;
        await route.fulfill({
          json: {
            ok: true,
            status_code: null,
            message: 'modbus connection ok',
          },
        });
      },
    );

    const row = page.getByTestId('connection-row-conn_modbus_xyz789');
    await row.getByLabel('row actions').click();
    await row.getByRole('button', { name: 'Test' }).click();
    await expect.poll(() => testCalls).toBeGreaterThan(0);

    // ── Delete action ──────────────────────────────────────────────
    let deleteCalls = 0;
    await page.route(
      '**/api/v1/admin/connections/conn_modbus_xyz789',
      async (route, req) => {
        if (req.method() === 'DELETE') {
          deleteCalls += 1;
          connections = connections.filter(c => c.id !== 'conn_modbus_xyz789');
          await route.fulfill({ json: null, status: 200 });
          return;
        }
        await route.continue();
      },
    );

    page.once('dialog', d => d.accept());
    await row.getByLabel('row actions').click();
    await row.getByRole('button', { name: 'Delete' }).click();
    await expect.poll(() => deleteCalls).toBeGreaterThan(0);
    await expect(
      page.getByTestId('connection-row-conn_modbus_xyz789'),
    ).not.toBeVisible({ timeout: 10_000 });
  });

  test('opcua: add → test (mocked) → delete', async ({ page }) => {
    let connections: Array<Record<string, unknown>> = [];
    let createdBody: Record<string, unknown> | null = null;

    // Broad catch-all FIRST so specific routes registered LATER win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/**', async (route, req) => {
      const url = req.url();
      const method = req.method();
      // Per-id GET
      const idMatch = url.match(/\/connections\/(conn_opcua_[a-z0-9]+)$/);
      if (method === 'GET' && idMatch) {
        const found = connections.find(c => c.id === idMatch[1]);
        if (found) {
          await route.fulfill({ json: found });
        } else {
          await route.fulfill({ status: 404, json: {} });
        }
        return;
      }
      // Per-id DELETE (catch-all — narrower handler added later wins).
      if (method === 'DELETE' && idMatch) {
        connections = connections.filter(c => c.id !== idMatch[1]);
        await route.fulfill({ json: null, status: 200 });
        return;
      }
      // Per-id /test (POST)
      const testMatch = url.match(/\/connections\/(conn_opcua_[a-z0-9]+)\/test$/);
      if (method === 'POST' && testMatch) {
        await route.fulfill({
          json: { ok: true, status_code: null, message: 'opcua state=0 status=Good' },
        });
        return;
      }
      // List endpoint
      if (method === 'GET' && /\/connections\/(\?.*)?$/.test(url)) {
        await route.fulfill({ json: connections });
        return;
      }
      // Create endpoint
      if (method === 'POST' && /\/connections\/(\?.*)?$/.test(url)) {
        createdBody = JSON.parse(req.postData() ?? '{}');
        const created = {
          id: 'conn_opcua_pqr456',
          kind: 'connection',
          protocol: 'opcua',
          project_id: 'default',
          display_name: 'e2e-opcua-plant',
          tags: ['industrial'],
          credentials_backend: { kind: 'local', config: {} },
          status: 'ready',
          last_tested_at: null,
          last_test_ok: null,
          is_default: true,
          created_at: '2026-05-26T00:00:00+00:00',
          updated_at: '2026-05-26T00:00:00+00:00',
          last_error: null,
          protocol_data: {
            endpoint_url: 'opc.tcp://server.example.com:4840',
            security_mode: 'None',
            security_policy: 'None',
            auth_mode: 'anonymous',
            username: '',
            password: '',
            operations: [],
            sandbox_tier_override: null,
          },
        };
        connections.push(created);
        await route.fulfill({ json: created });
        return;
      }
      await route.continue();
    });

    // Specific routes registered LAST so they win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [
          { id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] },
          { id: 'opcua', display_name: 'OPC UA', sensitive_fields: ['password'] },
        ],
      }));
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [{ id: 'local', display_name: 'Local encrypted file' }],
      }));

    await page.goto('/connections');
    await expect(page.getByTestId('filter-bar')).toBeVisible({ timeout: 10_000 });

    // Open Add modal
    await page.getByTestId('add-connection-btn').click();
    await expect(page.getByTestId('add-connection-modal')).toBeVisible();

    // Step 1: pick OPC UA
    await page.getByTestId('protocol-pick-opcua').click();

    // Step 2: fill the form (anonymous auth — keeps the e2e simple)
    await page.getByTestId('display-name-input').fill('e2e-opcua-plant');
    await page.getByTestId('opcua-endpoint-url').fill('opc.tcp://server.example.com:4840');
    await page.getByTestId('step-2').getByRole('button', { name: 'Next' }).click();

    // Step 3: defaults are fine
    await page.getByTestId('tags-input').fill('industrial');
    await page.getByTestId('submit-add-connection').click();

    // Modal closes
    await expect(page.getByTestId('add-connection-modal')).not.toBeVisible({ timeout: 10_000 });

    // New row visible
    await expect(page.getByText('e2e-opcua-plant')).toBeVisible();

    // Verify the create payload shape
    expect(createdBody).not.toBeNull();
    const body = createdBody as unknown as { protocol: string; protocol_data: Record<string, unknown> };
    expect(body.protocol).toBe('opcua');
    expect(body.protocol_data.endpoint_url).toBe('opc.tcp://server.example.com:4840');
    expect(body.protocol_data.auth_mode).toBe('anonymous');

    // ── Test action ────────────────────────────────────────────────
    let testCalls = 0;
    await page.route(
      '**/api/v1/admin/connections/conn_opcua_pqr456/test',
      async (route) => {
        testCalls += 1;
        await route.fulfill({
          json: {
            ok: true,
            status_code: null,
            message: 'opcua state=0 status=Good',
          },
        });
      },
    );

    const row = page.getByTestId('connection-row-conn_opcua_pqr456');
    await row.getByLabel('row actions').click();
    await row.getByRole('button', { name: 'Test' }).click();
    await expect.poll(() => testCalls).toBeGreaterThan(0);

    // ── Delete action ──────────────────────────────────────────────
    let deleteCalls = 0;
    await page.route(
      '**/api/v1/admin/connections/conn_opcua_pqr456',
      async (route, req) => {
        if (req.method() === 'DELETE') {
          deleteCalls += 1;
          connections = connections.filter(c => c.id !== 'conn_opcua_pqr456');
          await route.fulfill({ json: null, status: 200 });
          return;
        }
        await route.continue();
      },
    );

    page.once('dialog', d => d.accept());
    await row.getByLabel('row actions').click();
    await row.getByRole('button', { name: 'Delete' }).click();
    await expect.poll(() => deleteCalls).toBeGreaterThan(0);
    await expect(
      page.getByTestId('connection-row-conn_opcua_pqr456'),
    ).not.toBeVisible({ timeout: 10_000 });
  });

  test('add connection with doppler backend', async ({ page }) => {
    let createdBody: Record<string, unknown> | null = null;
    await page.route('**/api/v1/admin/connections/**', async (route, req) => {
      const url = req.url();
      const method = req.method();
      if (method === 'POST' && /\/connections\/(\?.*)?$/.test(url)) {
        createdBody = JSON.parse(req.postData() ?? '{}');
        await route.fulfill({
          json: {
            id: 'conn_test', kind: 'connection', protocol: 'http',
            project_id: 'default',
            display_name: 'Test Doppler', tags: [],
            credentials_backend: { kind: 'doppler', config: {} },
            status: 'ready',
            last_tested_at: null, last_test_ok: null, is_default: false,
            created_at: '2026-05-25T00:00:00+00:00',
            updated_at: '2026-05-25T00:00:00+00:00',
            last_error: null, protocol_data: {},
          },
        });
        return;
      }
      await route.fulfill({ json: [] });
    });
    // Specific routes registered LAST so they win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [{ id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] }],
      }));
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [
          { id: 'local', display_name: 'Local encrypted file' },
          { id: 'doppler', display_name: 'Doppler' },
        ],
      }));

    await page.goto('/connections');
    await page.getByTestId('add-connection-btn').click();
    await page.getByTestId('protocol-pick-http').click();
    await page.getByTestId('display-name-input').fill('Test Doppler');
    await page.getByTestId('base-url-input').fill('https://api.example.com');
    await page.getByTestId('step-2').getByRole('button', { name: 'Next' }).click();
    await page.getByTestId('backend-select').selectOption('doppler');
    await page.getByTestId('doppler-service-token').fill('dp.st.dev.abc123');
    await page.getByTestId('doppler-project').fill('sagewai');
    await page.getByTestId('doppler-config').fill('prd');
    await page.getByTestId('doppler-name-prefix').fill('SPOTIFY_MARKETING');
    await page.getByTestId('submit-add-connection').click();

    await expect(page.getByTestId('add-connection-modal')).not.toBeVisible({ timeout: 10_000 });

    expect(createdBody).not.toBeNull();
    const body = createdBody as unknown as { credentials_backend: { kind: string; config: Record<string, unknown> } };
    expect(body.credentials_backend.kind).toBe('doppler');
    expect(body.credentials_backend.config.project).toBe('sagewai');
    expect(body.credentials_backend.config.name_prefix).toBe('SPOTIFY_MARKETING');
  });

  test('websocket: add → test (mocked) → delete', async ({ page }) => {
    let connections: Array<Record<string, unknown>> = [];
    let createdBody: Record<string, unknown> | null = null;

    // Broad catch-all FIRST so specific routes registered LATER win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/**', async (route, req) => {
      const url = req.url();
      const method = req.method();
      const idMatch = url.match(/\/connections\/(conn_websocket_[a-z0-9]+)$/);
      if (method === 'GET' && idMatch) {
        const found = connections.find(c => c.id === idMatch[1]);
        if (found) {
          await route.fulfill({ json: found });
        } else {
          await route.fulfill({ status: 404, json: {} });
        }
        return;
      }
      if (method === 'DELETE' && idMatch) {
        connections = connections.filter(c => c.id !== idMatch[1]);
        await route.fulfill({ json: null, status: 200 });
        return;
      }
      const testMatch = url.match(/\/connections\/(conn_websocket_[a-z0-9]+)\/test$/);
      if (method === 'POST' && testMatch) {
        await route.fulfill({
          json: { ok: true, status_code: null, message: 'websocket handshake complete' },
        });
        return;
      }
      if (method === 'GET' && /\/connections\/(\?.*)?$/.test(url)) {
        await route.fulfill({ json: connections });
        return;
      }
      if (method === 'POST' && /\/connections\/(\?.*)?$/.test(url)) {
        createdBody = JSON.parse(req.postData() ?? '{}');
        const created = {
          id: 'conn_websocket_xyz789',
          kind: 'connection',
          protocol: 'websocket',
          project_id: 'default',
          display_name: 'e2e-websocket-market-data',
          tags: ['finance'],
          credentials_backend: { kind: 'local', config: {} },
          status: 'ready',
          last_tested_at: null,
          last_test_ok: null,
          is_default: true,
          created_at: '2026-05-26T00:00:00+00:00',
          updated_at: '2026-05-26T00:00:00+00:00',
          last_error: null,
          protocol_data: {
            url: 'wss://gateway.example.com/ws',
            headers: {},
            auth_header_name: 'Authorization',
            auth_header_value: '',
            default_timeout_seconds: 30,
            operations: [],
            sandbox_tier_override: null,
          },
        };
        connections.push(created);
        await route.fulfill({ json: created });
        return;
      }
      await route.continue();
    });

    // Specific routes registered LAST so they win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [
          { id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] },
          { id: 'websocket', display_name: 'WebSocket', sensitive_fields: ['auth_header_value'] },
        ],
      }));
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [{ id: 'local', display_name: 'Local encrypted file' }],
      }));

    await page.goto('/connections');
    await expect(page.getByTestId('filter-bar')).toBeVisible({ timeout: 10_000 });

    // Open Add modal
    await page.getByTestId('add-connection-btn').click();
    await expect(page.getByTestId('add-connection-modal')).toBeVisible();

    // Step 1: pick WebSocket
    await page.getByTestId('protocol-pick-websocket').click();

    // Step 2: fill the form (no auth — keeps the e2e simple)
    await page.getByTestId('display-name-input').fill('e2e-websocket-market-data');
    await page.getByTestId('websocket-url').fill('wss://gateway.example.com/ws');
    await page.getByTestId('step-2').getByRole('button', { name: 'Next' }).click();

    // Step 3: defaults are fine
    await page.getByTestId('tags-input').fill('finance');
    await page.getByTestId('submit-add-connection').click();

    // Modal closes
    await expect(page.getByTestId('add-connection-modal')).not.toBeVisible({ timeout: 10_000 });

    // New row visible
    await expect(page.getByText('e2e-websocket-market-data')).toBeVisible();

    // Verify the create payload shape
    expect(createdBody).not.toBeNull();
    const body = createdBody as unknown as { protocol: string; protocol_data: Record<string, unknown> };
    expect(body.protocol).toBe('websocket');
    expect(body.protocol_data.url).toBe('wss://gateway.example.com/ws');
    expect(body.protocol_data.auth_header_name).toBe('Authorization');

    // ── Test action ────────────────────────────────────────────────
    let testCalls = 0;
    await page.route(
      '**/api/v1/admin/connections/conn_websocket_xyz789/test',
      async (route) => {
        testCalls += 1;
        await route.fulfill({
          json: {
            ok: true,
            status_code: null,
            message: 'websocket handshake complete',
          },
        });
      },
    );

    const row = page.getByTestId('connection-row-conn_websocket_xyz789');
    await row.getByLabel('row actions').click();
    await row.getByRole('button', { name: 'Test' }).click();
    await expect.poll(() => testCalls).toBeGreaterThan(0);

    // ── Delete action ──────────────────────────────────────────────
    let deleteCalls = 0;
    await page.route(
      '**/api/v1/admin/connections/conn_websocket_xyz789',
      async (route, req) => {
        if (req.method() === 'DELETE') {
          deleteCalls += 1;
          connections = connections.filter(c => c.id !== 'conn_websocket_xyz789');
          await route.fulfill({ json: null, status: 200 });
          return;
        }
        await route.continue();
      },
    );

    page.once('dialog', d => d.accept());
    await row.getByLabel('row actions').click();
    await row.getByRole('button', { name: 'Delete' }).click();
    await expect.poll(() => deleteCalls).toBeGreaterThan(0);
    await expect(
      page.getByTestId('connection-row-conn_websocket_xyz789'),
    ).not.toBeVisible({ timeout: 10_000 });
  });

  test('mqtt: add → open panel → subscribe → unsubscribe (mocked)', async ({ page }) => {
    let connections: Array<Record<string, unknown>> = [];
    let createdBody: Record<string, unknown> | null = null;
    let subscriptions: Array<Record<string, unknown>> = [];
    let subscribeCalls = 0;
    let unsubscribeCalls = 0;

    // MQTT subscription sub-routes (most specific — register FIRST so the
    // generic catch-all registered LATER does NOT shadow them; Playwright
    // tries routes LIFO, so generic last = generic wins ordering is wrong.
    // We register specific FIRST then generic; but LIFO means generic wins.
    // To be safe we handle mqtt sub-routes inside the generic catch-all.)

    // Broad catch-all FIRST so specific routes registered LATER win (LIFO).
    await page.route('**/api/v1/admin/connections/**', async (route, req) => {
      const url = req.url();
      const method = req.method();

      // MQTT subscription routes (handled in the catch-all to keep ordering
      // deterministic regardless of registration order).
      if (method === 'GET' && /\/mqtt\/subscriptions(\?.*)?$/.test(url)) {
        await route.fulfill({ json: subscriptions });
        return;
      }
      const subMatch = url.match(/\/mqtt\/([a-z0-9_]+)\/subscribe$/);
      if (method === 'POST' && subMatch) {
        subscribeCalls++;
        const body = JSON.parse(req.postData() ?? '{}');
        const subId = 'sub-mock-1';
        subscriptions.push({
          subscription_id: subId,
          connection_id: subMatch[1],
          status: 'active',
          buffer_depth: 0,
          bytes_buffered: 0,
          overflow_dropped: 0,
          oversized_dropped: 0,
          global_pressure_dropped: 0,
          last_event_at: null,
          last_drain_at: 0,
          created_at: 0,
          topic_filter: body.topic_filter,
        });
        await route.fulfill({ json: { subscription_id: subId } });
        return;
      }
      const unsubMatch = url.match(/\/mqtt\/subscriptions\/([a-z0-9-]+)$/);
      if (method === 'DELETE' && unsubMatch) {
        unsubscribeCalls++;
        subscriptions = subscriptions.filter(
          s => s.subscription_id !== unsubMatch[1],
        );
        await route.fulfill({ json: null, status: 200 });
        return;
      }

      // Per-id GET
      const idMatch = url.match(/\/connections\/(conn_mqtt_[a-z0-9]+)$/);
      if (method === 'GET' && idMatch) {
        const found = connections.find(c => c.id === idMatch[1]);
        await route.fulfill(found ? { json: found } : { status: 404, json: {} });
        return;
      }
      // List endpoint
      if (method === 'GET' && /\/connections\/(\?.*)?$/.test(url)) {
        await route.fulfill({ json: connections });
        return;
      }
      // Create endpoint
      if (method === 'POST' && /\/connections\/(\?.*)?$/.test(url)) {
        createdBody = JSON.parse(req.postData() ?? '{}');
        const created = {
          id: 'conn_mqtt_abc123',
          kind: 'connection',
          protocol: 'mqtt',
          project_id: 'default',
          display_name: 'e2e-mqtt-broker',
          tags: ['fleet'],
          credentials_backend: { kind: 'local', config: {} },
          status: 'ready',
          last_tested_at: null,
          last_test_ok: null,
          is_default: true,
          created_at: '2026-05-29T00:00:00+00:00',
          updated_at: '2026-05-29T00:00:00+00:00',
          last_error: null,
          protocol_data: {
            host: 'broker.example.com',
            port: 1883,
            transport: 'tcp',
            client_id: '',
            mqtt_version: '5.0',
            username: '',
            password: '',
            keepalive_seconds: 60,
            tls_ca_cert: '',
            sandbox_tier_override: null,
          },
        };
        connections.push(created);
        await route.fulfill({ json: created });
        return;
      }
      await route.continue();
    });

    // Specific routes registered LAST so they win (Playwright LIFO).
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [
          { id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] },
          { id: 'mqtt', display_name: 'MQTT', sensitive_fields: ['password'] },
        ],
      }));
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [{ id: 'local', display_name: 'Local encrypted file' }],
      }));

    await page.goto('/connections');
    await expect(page.getByTestId('filter-bar')).toBeVisible({ timeout: 10_000 });

    // Open Add modal
    await page.getByTestId('add-connection-btn').click();
    await expect(page.getByTestId('add-connection-modal')).toBeVisible();

    // Step 1: pick MQTT
    await page.getByTestId('protocol-pick-mqtt').click();

    // Step 2: fill the form
    await page.getByTestId('display-name-input').fill('e2e-mqtt-broker');
    await page.getByTestId('mqtt-host').fill('broker.example.com');
    await page.getByTestId('step-2').getByRole('button', { name: 'Next' }).click();

    // Step 3: defaults are fine
    await page.getByTestId('tags-input').fill('fleet');
    await page.getByTestId('submit-add-connection').click();

    // Modal closes + new row visible
    await expect(page.getByTestId('add-connection-modal')).not.toBeVisible({ timeout: 10_000 });
    await expect(page.getByText('e2e-mqtt-broker')).toBeVisible();

    // Verify the create payload shape
    expect(createdBody).not.toBeNull();
    const body = createdBody as unknown as { protocol: string; protocol_data: Record<string, unknown> };
    expect(body.protocol).toBe('mqtt');
    expect(body.protocol_data.host).toBe('broker.example.com');
    expect(body.protocol_data.port).toBe(1883);

    // Open the detail drawer (click the row) → the MQTT panel renders.
    await page.getByTestId('connection-row-conn_mqtt_abc123').click();
    await expect(page.getByTestId('mqtt-panel')).toBeVisible();

    // Subscribe to a topic filter.
    await page.getByTestId('mqtt-new-topic-filter').fill('fleet/+/telemetry');
    await page.getByTestId('mqtt-add-subscription-button').click();
    await expect.poll(() => subscribeCalls).toBeGreaterThan(0);

    // The new subscription appears in the table.
    await expect(page.getByTestId('mqtt-unsubscribe-sub-mock-1')).toBeVisible({ timeout: 5000 });

    // Unsubscribe.
    await page.getByTestId('mqtt-unsubscribe-sub-mock-1').click();
    await expect.poll(() => unsubscribeCalls).toBeGreaterThan(0);
    await expect(page.getByTestId('mqtt-unsubscribe-sub-mock-1')).not.toBeVisible({ timeout: 5000 });
  });

  // ── Connection import / export ──────────────────────────────────

  test('export: open dropdown and download YAML (mocked)', async ({ page }) => {
    // Minimal mocks so the page renders.
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [{ id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] }],
      }),
    );
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [{ id: 'local', display_name: 'Local encrypted file' }],
      }),
    );
    await page.route('**/api/v1/admin/connections/?**', route =>
      route.fulfill({ json: [] }),
    );
    await page.route('**/api/v1/admin/connections/', route =>
      route.fulfill({ json: [] }),
    );

    // Mock the export endpoint
    await page.route('**/api/v1/admin/connections/export*', route => {
      void route.fulfill({
        status: 200,
        contentType: 'application/yaml; charset=utf-8',
        headers: { 'Content-Disposition': 'attachment; filename="test.yaml"' },
        body: 'version: 1\nsecrets_mode: redacted\nconnections: []\n',
      });
    });

    await page.goto('/connections');
    await page.getByTestId('export-yaml-button').click();
    await expect(page.getByTestId('export-download-button')).toBeVisible();

    // Set up download promise BEFORE clicking
    const downloadPromise = page.waitForEvent('download');
    await page.getByTestId('export-download-button').click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain('.yaml');
  });

  test('import: file upload + dry-run + confirm (mocked)', async ({ page }) => {
    // Minimal mocks
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [{ id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] }],
      }),
    );
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [{ id: 'local', display_name: 'Local encrypted file' }],
      }),
    );
    await page.route('**/api/v1/admin/connections/?**', route =>
      route.fulfill({ json: [] }),
    );
    await page.route('**/api/v1/admin/connections/', route =>
      route.fulfill({ json: [] }),
    );

    let importCallCount = 0;
    await page.route('**/api/v1/admin/connections/import*', (route, request) => {
      importCallCount++;
      const isDryRun = request.url().includes('dry_run=true');
      void route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          dry_run: isDryRun,
          created: [
            { id: 'conn-001', protocol: 'http', display_name: 'imported-http' },
          ],
          updated: [],
          skipped: [],
          errors: [],
        }),
      });
    });

    await page.goto('/connections');
    await page.getByTestId('import-yaml-button').click();
    await expect(page.getByTestId('import-modal')).toBeVisible();

    const yamlContent = `version: 1
secrets_mode: redacted
connections:
  - protocol: http
    display_name: imported-http
    tags: []
    credentials_backend:
      kind: local
    is_default: true
    protocol_data:
      base_url: https://a.com
      auth:
        kind: none
`;
    await page.getByTestId('import-file-input').setInputFiles({
      name: 'test.yaml',
      mimeType: 'application/yaml',
      buffer: Buffer.from(yamlContent),
    });

    // Dry-run
    await page.getByTestId('import-dry-run-button').click();
    await expect(page.getByTestId('import-result')).toBeVisible();
    await expect(page.getByText(/1 to create/)).toBeVisible();

    // Confirm
    await page.getByTestId('import-confirm-button').click();
    await expect.poll(() => importCallCount).toBeGreaterThanOrEqual(2);
  });

  test('import: rejects placeholder-mode YAML before submission', async ({
    page,
  }) => {
    // Minimal mocks — the import call must NEVER be made when placeholder
    // mode is detected client-side.
    await page.route('**/api/v1/admin/connections/protocols', route =>
      route.fulfill({
        json: [{ id: 'http', display_name: 'HTTP / REST', sensitive_fields: [] }],
      }),
    );
    await page.route('**/api/v1/admin/connections/backends', route =>
      route.fulfill({
        json: [{ id: 'local', display_name: 'Local encrypted file' }],
      }),
    );
    await page.route('**/api/v1/admin/connections/?**', route =>
      route.fulfill({ json: [] }),
    );
    await page.route('**/api/v1/admin/connections/', route =>
      route.fulfill({ json: [] }),
    );

    let importCallCount = 0;
    await page.route('**/api/v1/admin/connections/import*', route => {
      importCallCount++;
      void route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          dry_run: false,
          created: [],
          updated: [],
          skipped: [],
          errors: [],
        }),
      });
    });

    await page.goto('/connections');
    await page.getByTestId('import-yaml-button').click();
    await expect(page.getByTestId('import-modal')).toBeVisible();

    const placeholderYaml = `version: 1
secrets_mode: placeholder
connections:
  - protocol: oauth2
    display_name: spotify
    tags: []
    credentials_backend:
      kind: local
    is_default: true
    protocol_data:
      provider: spotify
      client_id: id
      client_secret: \${SPOTIFY_CLIENT_SECRET}
      redirect_uri: http://localhost/callback
      requested_scopes: []
      granted_scopes: []
      tokens: null
`;
    await page.getByTestId('import-file-input').setInputFiles({
      name: 'placeholder.yaml',
      mimeType: 'application/yaml',
      buffer: Buffer.from(placeholderYaml),
    });

    // Error banner appears with the CLI hint.
    await expect(page.getByTestId('import-error')).toBeVisible();
    await expect(page.getByTestId('import-error')).toContainText(
      /placeholder-mode imports must use the cli/i,
    );

    // Both action buttons stay disabled (file was cleared).
    await expect(page.getByTestId('import-dry-run-button')).toBeDisabled();
    await expect(page.getByTestId('import-submit-button')).toBeDisabled();

    // And no import call was made.
    expect(importCallCount).toBe(0);
  });
});
