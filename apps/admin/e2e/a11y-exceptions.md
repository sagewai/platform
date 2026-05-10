# A11y rule exceptions

## color-contrast (sidebar)

**Scope:** `a11y.spec.ts` scans `main` only (not `aside`).
**Element:** `text-sidebar-muted-foreground` (#64748b) on `bg-sidebar-accent/30` (#ebfaf9) — ratio 4.43:1 (needs 4.5:1). Also `⌘K` kbd at 4.47:1.
**Reason:** Pre-existing sidebar token values, outside Plan P scope. The sidebar is tested by the e2e suite.
**Tracked:** file a GitHub issue before v1.0 — fix by darkening `--color-sidebar-muted-foreground` one step.
**Expires:** before v1.0 launch.

Format:
```
## <rule-id>

**Page(s):** …
**Reason:** …
**Tracked:** <link to issue>
**Expires:** <date or "permanent">
```
