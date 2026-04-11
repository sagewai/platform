// Tailwind 4 uses CSS-only @theme directives in globals.css.
// This file exists only so tooling that expects a config (e.g. shadcn CLI preflight)
// can find one. Real configuration lives in app/globals.css and tokens/src/index.css.
import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './hooks/**/*.{ts,tsx}',
  ],
  theme: { extend: {} },
  plugins: [],
};

export default config;
