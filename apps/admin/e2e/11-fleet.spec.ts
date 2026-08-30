import { test, expect } from '@playwright/test';
import type { FleetWorker } from '../utils/types';

const pendingWorker: FleetWorker = {
  id: 'worker-mac-mini',
  name: 'Mac mini',
  org_id: 'org-test',
  project_id: 'project-test',
  capabilities: {
    models_supported: [],
    models_canonical: [],
    capability_names: ['runtime.codex', 'filesystem.write'],
    max_concurrent: 1,
    labels: { device: 'mac-mini' },
    pool: 'coding',
    sdk_version: 'test',
  },
  approval_status: 'pending',
  last_heartbeat: '2026-08-30T18:00:00Z',
  last_probe_at: null,
  probe_status: null,
  registered_at: '2026-08-30T18:00:00Z',
  approved_at: null,
  approved_by: null,
};

test.describe('Fleet', () => {
  test('/fleet loads worker list', async ({ page }) => {
    await page.goto('/fleet');
    await page.waitForTimeout(2000);
    await expect(page.locator('body')).not.toContainText('Backend not reachable');
    await expect(page.locator('body')).not.toContainText('Application error');
  });

  test('creates an enrollment key and shows its secret once', async ({ page }) => {
    await page.goto('/fleet/enrollment-keys');
    await page.getByRole('button', { name: 'Create Key' }).click();
    await page.getByLabel('Name *').fill('Laptop and Mac mini');
    await page.getByLabel('Max Uses (optional)').fill('2');
    await page.getByLabel('Allowed Pools (comma-separated, optional)').fill('default');
    await page.getByRole('button', { name: 'Create Key', exact: true }).last().click();

    await expect(page.getByText('Save this key -- it will not be shown again')).toBeVisible();
    await expect(page.getByText('Laptop and Mac mini')).toBeVisible();
    await expect(page.getByText('default', { exact: true })).toBeVisible();
  });

  test('approves a worker and opens its live detail', async ({ page }) => {
    let worker = { ...pendingWorker };
    await page.route('**/api/v1/fleet/workers**', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/approve')) {
        worker = {
          ...worker,
          approval_status: 'approved',
          approved_at: '2026-08-30T18:01:00Z',
          approved_by: 'admin',
        };
        await route.fulfill({ json: { worker } });
        return;
      }
      if (url.pathname.endsWith(`/${worker.id}`)) {
        await route.fulfill({ json: { worker } });
        return;
      }
      await route.fulfill({ json: { workers: [worker], total: 1 } });
    });

    await page.goto('/fleet');
    await expect(page.getByText('Mac mini')).toBeVisible();
    await page.getByRole('button', { name: 'Approve' }).click();
    await expect(page.getByText('approved', { exact: true })).toBeVisible();

    await page.getByRole('link', { name: 'Mac mini' }).click();
    await expect(page.getByRole('heading', { name: 'Mac mini' })).toBeVisible();
    await page.getByRole('button', { name: 'Capabilities' }).click();
    await expect(page.getByText('runtime.codex', { exact: true })).toBeVisible();
  });
});
