/** @type {import('next').NextConfig} */

const isDev = process.env.NODE_ENV !== 'production';

// Derive the allowed API origin from the env var so the CSP works in all
// environments without hardcoding localhost.  The var may include a path
// (e.g. https://api.example.com/admin) — strip it to origin only.
function _apiOrigin() {
  const raw = process.env.NEXT_PUBLIC_ADMIN_API_URL || '';
  if (!raw) return '';
  try {
    const { protocol, host } = new URL(raw);
    return `${protocol}//${host}`;
  } catch {
    // Fallback: use the raw value as-is (handles bare host:port strings)
    return raw;
  }
}

const _apiOriginValue = _apiOrigin();
const _connectSrcExtra = _apiOriginValue && _apiOriginValue !== "'self'" ? ` ${_apiOriginValue}` : '';
// Dev fallback: allow localhost origins when no env var is set
const _connectSrcDev = isDev && !_apiOriginValue
  ? ' http://localhost:8000 http://localhost:8001 ws://localhost:3008'
  : '';

const securityHeaders = [
  { key: 'X-DNS-Prefetch-Control', value: 'on' },
  { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
  {
    key: 'Content-Security-Policy',
    value: [
      "default-src 'self'",
      `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ''}`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self'",
      `connect-src 'self'${_connectSrcExtra}${_connectSrcDev}`,
      "frame-ancestors 'none'",
    ].join('; '),
  },
  { key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' },
];

const nextConfig = {
  output: 'standalone',
  // @sagecurator/ui was decommissioned — compat layer is at components/ui/legacy.tsx
  transpilePackages: [],
  async headers() {
    return [{ source: '/(.*)', headers: securityHeaders }];
  },
  async redirects() {
    return [
      { source: '/settings/account', destination: '/account/profile', permanent: true },
      { source: '/settings/tokens', destination: '/account/tokens', permanent: true },
      { source: '/settings/notifications', destination: '/account/notifications', permanent: true },
      { source: '/settings/organization', destination: '/system/organization', permanent: true },
      { source: '/settings/models', destination: '/system/models', permanent: true },
      { source: '/settings/services', destination: '/system/connectors', permanent: true },
      { source: '/settings/infrastructure', destination: '/system/infrastructure', permanent: true },
      { source: '/settings/projects', destination: '/system/projects', permanent: true },
      { source: '/settings/billing', destination: '/system/billing', permanent: true },
      { source: '/settings/health', destination: '/system/health', permanent: true },
    ];
  },
};

module.exports = nextConfig;
