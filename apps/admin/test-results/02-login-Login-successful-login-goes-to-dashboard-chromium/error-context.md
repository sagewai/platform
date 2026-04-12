# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: 02-login.spec.ts >> Login >> successful login goes to dashboard
- Location: e2e/02-login.spec.ts:23:7

# Error details

```
Error: expect(page).toHaveURL(expected) failed

Expected: "http://localhost:3808/"
Received: "http://localhost:3808/login"
Timeout:  10000ms

Call log:
  - Expect "toHaveURL" with timeout 10000ms
    14 × unexpected value "http://localhost:3808/login"

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - generic [ref=e5]:
    - generic [ref=e6]:
      - img "Sagewai" [ref=e7]
      - paragraph [ref=e8]: Sign in to your account
    - generic [ref=e9]:
      - generic [ref=e10]: Invalid email or password
      - generic [ref=e11]:
        - generic [ref=e12]:
          - generic [ref=e13]: Email
          - generic [ref=e14]:
            - img
            - textbox "Email" [ref=e15]:
              - /placeholder: you@example.com
              - text: admin@test.sagewai.dev
        - generic [ref=e16]:
          - generic [ref=e17]: Password
          - generic [ref=e18]:
            - img
            - textbox "Password" [ref=e19]:
              - /placeholder: ••••••••
              - text: TestPass123!
        - button "Sign In" [ref=e20]
      - link "Forgot password?" [ref=e22] [cursor=pointer]:
        - /url: /forgot-password
      - generic [ref=e23]:
        - text: Don't have an account?
        - link "Sign up" [ref=e24] [cursor=pointer]:
          - /url: /register
    - paragraph [ref=e25]: Sagewai v0.1.0
  - region "Notifications alt+T"
  - button "Open Next.js Dev Tools" [ref=e31] [cursor=pointer]:
    - img [ref=e32]
  - alert [ref=e35]
```

# Test source

```ts
  1  | import { test, expect } from '@playwright/test';
  2  | import { setupAndLogin } from './mock-api';
  3  | 
  4  | let credentials: { email: string; password: string };
  5  | 
  6  | test.beforeAll(async () => {
  7  |   credentials = await setupAndLogin();
  8  | });
  9  | 
  10 | test.describe('Login', () => {
  11 |   test('unauthenticated users are redirected to /login', async ({ page }) => {
  12 |     await page.goto('/');
  13 |     await expect(page).toHaveURL(/\/login/);
  14 |   });
  15 | 
  16 |   test('login form has email, password, and sign-in button', async ({ page }) => {
  17 |     await page.goto('/login');
  18 |     await expect(page.getByPlaceholder('you@example.com')).toBeVisible();
  19 |     await expect(page.getByPlaceholder('••••••••')).toBeVisible();
  20 |     await expect(page.getByRole('button', { name: 'Sign In' })).toBeVisible();
  21 |   });
  22 | 
  23 |   test('successful login goes to dashboard', async ({ page }) => {
  24 |     await page.goto('/login');
  25 |     await page.getByPlaceholder('you@example.com').fill(credentials.email);
  26 |     await page.getByPlaceholder('••••••••').fill(credentials.password);
  27 |     await page.getByRole('button', { name: 'Sign In' }).click();
> 28 |     await expect(page).toHaveURL('/', { timeout: 10_000 });
     |                        ^ Error: expect(page).toHaveURL(expected) failed
  29 |   });
  30 | 
  31 |   test('wrong password shows error', async ({ page }) => {
  32 |     await page.goto('/login');
  33 |     await page.getByPlaceholder('you@example.com').fill(credentials.email);
  34 |     await page.getByPlaceholder('••••••••').fill('wrongpassword');
  35 |     await page.getByRole('button', { name: 'Sign In' }).click();
  36 |     await expect(page.getByText(/Invalid email or password/)).toBeVisible();
  37 |   });
  38 | 
  39 |   test('shows forgot-password and sign-up links', async ({ page }) => {
  40 |     await page.goto('/login');
  41 |     await expect(page.getByText('Forgot password?')).toBeVisible();
  42 |     await expect(page.getByText('Sign up')).toBeVisible();
  43 |   });
  44 | });
  45 | 
```