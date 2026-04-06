# @sagewai/tokens

Sagewai design tokens for Tailwind CSS 4. Provides colors, spacing, typography, motion, and dark mode support as CSS custom properties.

## Install

```bash
npm install @sagewai/tokens
```

**Peer dependency:** Tailwind CSS 4+

## Usage

Import in your global CSS file:

```css
@import "@sagewai/tokens/src/index.css";
```

This gives you:

- **Tailwind 4** via `@import "tailwindcss"`
- **Design tokens** as CSS custom properties in a `@theme` block
- **Dark mode** via `[data-theme="dark"]` selector
- **Motion tokens** (durations, easing curves, keyframes)
- **Base styles** (body reset, scrollbar, focus rings)

## Tokens Included

### Colors

| Token | Light | Dark |
|-------|-------|------|
| `--color-primary` | `#0E7490` (Sage Teal) | `#26C6DA` |
| `--color-secondary` | `#B45309` (Circuit Gold) | `#FFB74D` |
| `--color-accent-purple` | `#7E22CE` | `#9C27B0` |
| `--color-accent-orange` | `#C2410C` | `#FF7043` |
| `--color-bg-page` | `#FFFFFF` | `#0A1628` |
| `--color-bg-surface` | `#F8FAFC` | `#111D35` |
| `--color-text-primary` | `#0F172A` | `#F0F4F8` |
| `--color-success` | `#15803D` | `#4CAF50` |
| `--color-error` | `#DC2626` | `#EF5350` |
| `--color-warning` | `#D97706` | `#FFB74D` |

### Spacing

`--spacing-xs` (4px), `--spacing-sm` (8px), `--spacing-md` (16px), `--spacing-lg` (24px), `--spacing-xl` (32px), `--spacing-2xl` (48px)

### Typography

- `--font-heading` / `--font-body`: Inter (system-ui fallback)
- `--font-mono`: JetBrains Mono (ui-monospace fallback)

### Radii

`--radius-sm` (6px), `--radius-md` (8px), `--radius-lg` (12px), `--radius-xl` (16px)

### Motion

`--duration-fast` (120ms), `--duration-base` (200ms), `--duration-slow` (350ms)

`--ease-standard`, `--ease-enter`, `--ease-exit`

## Dark Mode

Set `data-theme="dark"` on the root element:

```javascript
document.documentElement.setAttribute('data-theme', 'dark');
```

All color tokens are automatically overridden.

## Tailwind 4 Integration

Tokens are defined in a `@theme` block, so Tailwind 4 automatically generates utility classes:

- `--color-primary` becomes `text-primary`, `bg-primary`, `border-primary`
- `--spacing-md` becomes `p-md`, `m-md`, `gap-md`
- `--radius-lg` becomes `rounded-lg`

## Versioning

Token names are part of the public API. Renaming or removing a CSS custom property (e.g., `--color-primary`) is a **breaking change** and requires a major version bump. Adding new tokens is a minor version bump.

## License

MIT
