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
  transpilePackages: ['@sagecurator/ui'],
  async headers() {
    return [{ source: '/(.*)', headers: securityHeaders }];
  },
};

module.exports = nextConfig;
