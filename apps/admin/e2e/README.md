# Admin e2e tests

Playwright-based end-to-end tests for the Sagewai admin panel.

## Running

```bash
# All e2e tests (requires backend + frontend running — playwright starts them)
pnpm --filter @sagewai/admin test:e2e

# Accessibility tests only
pnpm --filter @sagewai/admin test:e2e:a11y

# Visual regression tests only
pnpm --filter @sagewai/admin test:e2e:visual

# Interactive mode
pnpm --filter @sagewai/admin test:e2e:ui
```

## Visual regression baselines

Baseline screenshots live in `e2e/__screenshots__/`. They cover 8 autopilot pages ×
3 viewports (sm/md/lg) × 2 themes (light/dark) = 48 snapshots.

### Updating baselines

When a visual change is intentional:

```bash
pnpm --filter @sagewai/admin test:e2e:visual -- --update-snapshots
git add e2e/__screenshots__/
git commit -m "chore(admin): update visual regression baselines"
```

Reviewers approve the visual diff during code review.

### Snapshot settings

- Max diff: 1% pixel ratio (`maxDiffPixelRatio: 0.01`) to absorb antialiasing noise.
- All animations/transitions are disabled at capture time for determinism.
- API endpoints are mocked via `e2e/fixtures/*.json`.

## Accessibility tests

`a11y.spec.ts` runs `@axe-core/playwright` against every autopilot page in both
themes and asserts zero WCAG 2.1 AA violations.

If a rule must be suppressed, document it in `a11y-exceptions.md` and use
`.disableRules([...])` with a code comment.
