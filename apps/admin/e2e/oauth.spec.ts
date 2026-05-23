import { test, expect } from '@playwright/test';

/**
 * OAuth tab — end-to-end happy path with the OAuth endpoints stubbed.
 *
 * The test never actually opens a popup or hits a real provider: it
 * intercepts every call under /api/v1/admin/connections/oauth/ and
 * walks the modal through provider-pick → credentials → authorize.
 *
 * Auth uses the storageState saved by `auth.setup.ts`.
 */

const PROVIDERS = [
  {
    id: 'spotify',
    display_name: 'Spotify',
    default_scopes: ['user-read-private', 'playlist-read-private'],
    docs_url: 'https://developer.spotify.com/documentation/general/guides/authorization/',
  },
];

const PENDING_CLIENT = {
  id: 'oauth_spotify_test',
  kind: 'oauth_client',
  project_id: 'default',
  provider: 'spotify',
  display_name: 'Test Spotify',
  redirect_uri: 'http://localhost:3808/api/v1/admin/connections/oauth/callback',
  requested_scopes: ['user-read-private', 'playlist-read-private'],
  granted_scopes: [],
  tokens: null,
  is_default: false,
  status: 'pending',
  last_error: null,
  created_at: '2026-05-23T12:00:00Z',
  updated_at: '2026-05-23T12:00:00Z',
};

const AUTHORIZED_CLIENT = {
  ...PENDING_CLIENT,
  granted_scopes: ['user-read-private'],
  tokens: {
    token_type: 'Bearer',
    expires_at: '2026-05-23T13:00:00Z',
    obtained_at: '2026-05-23T12:00:30Z',
    last_refreshed_at: null,
  },
  is_default: true,
  status: 'authorized',
};

test.describe('OAuth tab', () => {
  test('add Spotify OAuth client end-to-end', async ({ page }) => {
    // Block any popup window — modal calls window.open after Save+Authorize.
    await page.addInitScript(() => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (window as unknown as { open: (...args: unknown[]) => null }).open = () => null;
    });

    // ── Providers list ────────────────────────────────────────────────────
    await page.route('**/api/v1/admin/connections/oauth/providers', (route) =>
      route.fulfill({ json: PROVIDERS }),
    );

    // ── List + create endpoint (collection root) ──────────────────────────
    // List returns []  before POST, then [AUTHORIZED_CLIENT] after POST.
    // (React Strict Mode in dev fires the initial-load useEffect twice — both
    // calls must see the empty list so the empty state renders.)
    let posted = false;
    await page.route('**/api/v1/admin/connections/oauth/', async (route) => {
      const req = route.request();
      if (req.method() === 'GET') {
        if (!posted) {
          await route.fulfill({ json: [] });
        } else {
          await route.fulfill({ json: [AUTHORIZED_CLIENT] });
        }
        return;
      }
      // POST — create + start
      posted = true;
      await route.fulfill({
        json: {
          record: PENDING_CLIENT,
          authorize_url: 'about:blank',
          state: 'st-test-123',
        },
      });
    });

    // ── GET-by-id polling — first call pending, subsequent calls authorized.
    let getCalls = 0;
    await page.route(
      '**/api/v1/admin/connections/oauth/oauth_spotify_test',
      async (route) => {
        const req = route.request();
        if (req.method() !== 'GET') {
          await route.fulfill({ status: 405, body: '' });
          return;
        }
        getCalls++;
        // Return authorized on every poll so the test doesn't hang waiting.
        await route.fulfill({ json: AUTHORIZED_CLIENT });
      },
    );

    // ── Drive the UI ──────────────────────────────────────────────────────
    await page.goto('/connections');
    await page.getByRole('tab', { name: /oauth/i }).click();
    await expect(page.getByTestId('oauth-clients-empty')).toBeVisible({
      timeout: 10_000,
    });

    await page.getByTestId('oauth-add-button').click();

    // Step 1: pick provider (Spotify is the only option + auto-selected)
    await expect(page.getByTestId('oauth-provider-select')).toBeVisible();
    await expect(page.getByTestId('oauth-redirect-uri')).toContainText(
      '/api/v1/admin/connections/oauth/callback',
    );
    await page.getByTestId('oauth-next-button').click();

    // Step 2: credentials
    await page.locator('input[name="display_name"]').fill('Test Spotify');
    await page.locator('input[name="client_id"]').fill('cid-test');
    await page.locator('input[name="client_secret"]').fill('csec-test');
    await page.getByTestId('oauth-save-button').click();

    // Step 3: authorize → polls /oauth_spotify_test → returns authorized.
    await expect(page.getByTestId('oauth-authorize-step')).toBeVisible({
      timeout: 5_000,
    });
    // Wait for the polling to flip to "authorized" — message includes
    // the "connected" copy.
    await expect(
      page.getByText(/test spotify connected/i),
    ).toBeVisible({ timeout: 10_000 });
    expect(getCalls).toBeGreaterThanOrEqual(1);
  });
});
