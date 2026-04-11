# Sagewai brand assets

Canonical logo, icon, and favicon files for the Sagewai project. Every
package in this monorepo (admin, docs, vscode-extension) references these
files as the single source of truth. Do **not** vendor copies into app
directories — import from here.

## Files

| File | Purpose |
|---|---|
| `sagewai_logo.svg` | Primary wordmark for light backgrounds |
| `sagewai_logo_dark.svg` | Primary wordmark for dark backgrounds |
| `sagewai_logo.webp` | Raster wordmark, light |
| `sagewai_logo_dark.webp` | Raster wordmark, dark |
| `sagewai_icon.svg` | Square icon for light backgrounds |
| `sagewai_icon_dark.svg` | Square icon for dark backgrounds |
| `sagewai_icon.webp` | Raster square icon, light |
| `sagewai_icon_dark.webp` | Raster square icon, dark |
| `logo.svg` | Compact mark used inside the admin UI |
| `favicon.ico` | Browser favicon |

## Rule: prefer the wordmark

**When the layout can fit the wordmark logo, use the wordmark — not the
icon.** The icon is only a fallback for physically-constrained spaces
(e.g., the admin sidebar collapsed state, browser favicons, app tiles,
social-share avatars). Do not use the icon as a mobile-breakpoint
substitute for the logo.

## Usage

- **Name in prose:** Sagewai
- **Name in package/command names:** `sagewai`
- **Never mix** the two.
- **Trademark:** the Sagewai name, logo, and related marks are the property
  of Ali Arda Diri. See [`TRADEMARK.md`](../TRADEMARK.md) at the repo root
  for the full trademark policy.

## Licensing

The brand assets in this directory are **not** covered by the AGPL-3.0
license that applies to the source code. They are trademarked works of
Ali Arda Diri. Contributors may reference them in documentation and in
official builds, but may not use them to promote forks, derivative
products, or unaffiliated services without written permission.
