# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: 04-agent-templates.spec.ts >> Agent Templates >> shows at least one template card
- Location: e2e/04-agent-templates.spec.ts:14:7

# Error details

```
Error: browserContext.addCookies: cookies[0].value: expected string, got undefined
```

# Test source

```ts
  1   | /**
  2   |  * E2E test helpers — real backend, minimal browser-side mocks.
  3   |  *
  4   |  * The Playwright config starts both the backend (port 8000) and the
  5   |  * frontend (port 3808) automatically. The backend uses in-memory
  6   |  * state so tests are fast and deterministic.
  7   |  *
  8   |  * We only mock two things from the browser side:
  9   |  *  1. Auth cookie — so the proxy doesn't redirect to /login
  10  |  *  2. SSE event stream — to avoid hanging connections
  11  |  */
  12  | import type { Page } from '@playwright/test';
  13  | 
  14  | /**
  15  |  * Set the auth cookie from the real backend.
  16  |  * Call `loginAndGetToken()` first to get a valid token.
  17  |  */
  18  | export async function setAuthCookie(page: Page, token: string) {
> 19  |   await page.context().addCookies([
      |                        ^ Error: browserContext.addCookies: cookies[0].value: expected string, got undefined
  20  |     {
  21  |       name: 'sagewai_auth',
  22  |       value: token,
  23  |       domain: 'localhost',
  24  |       path: '/',
  25  |       httpOnly: true,
  26  |       sameSite: 'Lax' as const,
  27  |     },
  28  |   ]);
  29  | }
  30  | 
  31  | /**
  32  |  * Run the setup wizard via API and return an auth token.
  33  |  * Call this once in beforeAll to bootstrap the backend state.
  34  |  */
  35  | export async function setupAndLogin(opts?: {
  36  |   orgName?: string;
  37  |   email?: string;
  38  |   password?: string;
  39  | }): Promise<{ token: string; email: string; password: string }> {
  40  |   const email = opts?.email ?? 'admin@test.sagewai.dev';
  41  |   const password = opts?.password ?? 'TestPass123!';
  42  |   const orgName = opts?.orgName ?? 'E2E Test Org';
  43  | 
  44  |   // Check if setup is needed
  45  |   const statusRes = await fetch('http://localhost:8000/api/v1/setup/status');
  46  |   const status = await statusRes.json();
  47  | 
  48  |   if (status.setup_required) {
  49  |     // Run setup
  50  |     await fetch('http://localhost:8000/api/v1/setup', {
  51  |       method: 'POST',
  52  |       headers: { 'Content-Type': 'application/json' },
  53  |       body: JSON.stringify({
  54  |         org_name: orgName,
  55  |         org_slug: 'e2e-test',
  56  |         contact_email: email,
  57  |         timezone: 'UTC',
  58  |         app_name: 'E2E App',
  59  |         app_description: 'Automated testing',
  60  |         admin_name: 'E2E Admin',
  61  |         admin_email: email,
  62  |         admin_password: password,
  63  |       }),
  64  |     });
  65  |   }
  66  | 
  67  |   // Login
  68  |   const loginRes = await fetch('http://localhost:8000/api/v1/auth/login', {
  69  |     method: 'POST',
  70  |     headers: { 'Content-Type': 'application/json' },
  71  |     body: JSON.stringify({ email, password }),
  72  |   });
  73  |   const loginData = await loginRes.json();
  74  | 
  75  |   return { token: loginData.access_token, email, password };
  76  | }
  77  | 
  78  | /**
  79  |  * Authenticate the browser context — login fresh to get a valid
  80  |  * token (the backend only keeps one active token at a time).
  81  |  */
  82  | export async function authenticate(page: Page) {
  83  |   const { token } = await setupAndLogin();
  84  |   await setAuthCookie(page, token);
  85  | 
  86  |   // Mock SSE stream to avoid hanging connections
  87  |   await page.route('**/admin/events/stream', (route) =>
  88  |     route.fulfill({ body: '', contentType: 'text/event-stream' }),
  89  |   );
  90  | }
  91  | 
  92  | /**
  93  |  * Reset the backend to fresh-install state (for setup wizard tests).
  94  |  */
  95  | export async function resetBackendState() {
  96  |   const fs = await import('fs');
  97  |   const path = await import('path');
  98  |   const home = process.env.HOME ?? '/tmp';
  99  |   const stateFile = path.join(home, '.sagewai', 'admin-state.json');
  100 |   try {
  101 |     fs.unlinkSync(stateFile);
  102 |   } catch {
  103 |     // File doesn't exist — already clean
  104 |   }
  105 | }
  106 | 
```