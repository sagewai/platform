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

Baseline screenshots are generated under `e2e/visual.spec.ts-snapshots/`. They
cover the four console pages (`/board`, `/tasks`, `/decisions`, `/work`) × 3
viewports (sm/md/lg) × 2 themes (light/dark) = 24 snapshots. The directory is
git-ignored and platform-specific; reviewers compare local regenerated
snapshots instead of committing them to the repository.

### Updating baselines

When a visual change is intentional:

```bash
pnpm --filter @sagewai/admin test:e2e:visual -- --update-snapshots
```

### Snapshot settings

- Max diff: 1% pixel ratio (`maxDiffPixelRatio: 0.01`) to absorb antialiasing noise.
- All animations/transitions are disabled at capture time for determinism.

## Accessibility tests

`a11y.spec.ts` runs `@axe-core/playwright` against the four console pages
(`/board`, `/tasks`, `/decisions`, `/work`) in both themes and asserts zero
WCAG 2.1 AA violations.

If a rule must be suppressed, document it in `a11y-exceptions.md` and use
`.disableRules([...])` with a code comment.
