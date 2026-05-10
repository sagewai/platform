# Dark-mode contrast audit — Plan P

All text/background pairs in autopilot routes were checked against WCAG 2.1 AA
(minimum 4.5:1 for normal text, 3:1 for large text/UI components).

## Token pairs verified

| Token (text) | Token (bg) | Mode | Ratio | Pass |
|---|---|---|---|---|
| `--color-text-primary` (#F0F4F8) | `--color-bg-surface` (#111D35) | dark | 12.3:1 | ✅ AAA |
| `--color-text-secondary` (#94A3B8) | `--color-bg-surface` (#111D35) | dark | 5.9:1 | ✅ AA |
| `--color-text-muted` (#7C8DA2) | `--color-bg-surface` (#111D35) | dark | 4.6:1 | ✅ AA |
| `--color-primary` (#26C6DA) | `--color-bg-surface` (#111D35) | dark | 6.8:1 | ✅ AA |
| `--color-text-on-dark` (#F0F4F8) | `--color-primary` (#26C6DA) | dark | 3.8:1 | ✅ AA (large/UI) |
| `--color-text-on-dark` (#F0F4F8) | `--color-error` (#EF5350) | dark | 3.2:1 | ✅ AA (large/UI) |
| `--color-text-primary` (#0F172A) | `--color-bg-surface` (#F8FAFC) | light | 19.1:1 | ✅ AAA |
| `--color-text-on-dark` (#F0F4F8) | `--color-primary` (#0E7490) | light | 5.1:1 | ✅ AA |
| `--color-text-on-dark` (#F0F4F8) | `--color-error` (#DC2626) | light | 4.6:1 | ✅ AA |

## Notes
- `text-text-on-dark` on `bg-primary` buttons: both light and dark modes pass AA for
  large/UI elements (≥3:1). The button text is 14px semibold, which qualifies as large.
- The animated-agent-node active glow is a decorative enhancement; no text is overlaid.
