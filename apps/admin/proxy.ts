import { NextRequest, NextResponse } from 'next/server';

const ADMIN_API_URL =
  process.env.NEXT_PUBLIC_ADMIN_API_URL?.replace(/\/admin$/, '') ??
  'http://localhost:8000';

const AUTH_COOKIE = 'sagewai_auth';

/**
 * Whether a dev token is configured (via `make dev-setup`).
 * When present, the frontend uses this JWT for all API calls and
 * doesn't need the httpOnly auth cookie for route gating.
 */
const HAS_DEV_TOKEN = !!process.env.NEXT_PUBLIC_ADMIN_DEV_TOKEN;

/** Paths that don't require authentication (but still need setup check). */
const AUTH_PUBLIC = new Set(['/login', '/register', '/forgot-password']);

/**
 * Next.js proxy — runs server-side before every matched route. (Formerly
 * `middleware` in Next 15; renamed in Next 16.)
 *
 * Flow:
 *   1. If setup not done → redirect everything to /setup (except /setup itself).
 *   2. If setup done + on /setup → redirect to /.
 *   3. If setup done + no auth cookie + no dev token + not on auth page → redirect to /login.
 *   4. If setup done + auth cookie + on auth page → redirect to /.
 *   5. Otherwise → allow through.
 */
export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // ── 1. Check setup status ──────────────────────────────────────────────
  let setupRequired = false;
  try {
    const res = await fetch(`${ADMIN_API_URL}/api/v1/setup/status`, {
      next: { revalidate: 0 },
      headers: { 'Cache-Control': 'no-cache' },
    });
    if (res.ok) {
      const data = await res.json();
      setupRequired = !!data.setup_required;
    }
  } catch {
    // Backend unreachable — let the page render; the client-side
    // ConnectionProvider will detect this and show the error page.
    // Set a response header so the layout can show the error immediately
    // without waiting for a client-side health check.
    const res = NextResponse.next();
    res.headers.set('x-backend-status', 'unreachable');
    return res;
  }

  // ── 2. Setup required → only /setup is allowed ─────────────────────────
  if (setupRequired) {
    if (pathname === '/setup') return NextResponse.next();
    return NextResponse.redirect(new URL('/setup', request.url));
  }

  // ── 3. Setup done → block /setup ───────────────────────────────────────
  if (pathname === '/setup') {
    return NextResponse.redirect(new URL('/', request.url));
  }

  // ── 4. Auth check ──────────────────────────────────────────────────────
  // Dev token present (from `make dev-setup`) — auth is handled by the
  // JWT in the Authorization header, not by cookies. Allow through.
  if (HAS_DEV_TOKEN) {
    return NextResponse.next();
  }

  const hasAuth = request.cookies.has(AUTH_COOKIE);

  if (AUTH_PUBLIC.has(pathname)) {
    // Already logged in → redirect away from auth pages
    if (hasAuth) return NextResponse.redirect(new URL('/', request.url));
    return NextResponse.next();
  }

  if (!hasAuth) {
    return NextResponse.redirect(new URL('/login', request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    /*
     * Match all paths except:
     *  - _next/static, _next/image (Next.js internals)
     *  - favicon.ico, *.svg, *.png, *.jpg, etc. (static assets)
     */
    '/((?!_next/static|_next/image|favicon\\.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|woff2?|ttf|eot|map)$).*)',
  ],
};
