/** Shared fetch-based API client factory. */

export function createFetchClient(
  baseUrl: string,
  opts?: { getToken?: () => string | null; getProjectId?: () => string | null },
) {
  async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
    const isFormData = typeof FormData !== 'undefined' && init?.body instanceof FormData;
    const headers: Record<string, string> = {
      // Let the browser set Content-Type for FormData (includes boundary)
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...(init?.headers as Record<string, string>),
    };
    const token = opts?.getToken?.();
    if (token) headers['Authorization'] = `Bearer ${token}`;
    const projectId = opts?.getProjectId?.();
    if (projectId) headers['X-Project-ID'] = projectId;
    // Strip caller-supplied auth controls; this client decides them centrally.
    // Without explicit deletion, a stray `credentials: 'omit'` upstream — or
    // an HMR'd module that captured an older signature — silently drops
    // session cookies and produces 401s with no console error.
    const safeInit: RequestInit = { ...(init ?? {}) };
    delete (safeInit as { credentials?: RequestCredentials }).credentials;
    delete (safeInit as { headers?: HeadersInit }).headers;
    const res = await fetch(`${baseUrl}${path}`, {
      ...safeInit,
      headers,
      credentials: 'include',
    });
    // Dev-only diagnostic: surface the rare case where the request lands
    // anonymous (no Bearer token, no cookie reached the server) so the
    // failure is visible instead of presenting as a generic 401.
    if (process.env.NODE_ENV !== 'production' && res.status === 401) {
      const noAuthHeader = !token;
      const noCookie = typeof document !== 'undefined'
        && !document.cookie.includes('sagewai_auth=');
      if (noAuthHeader && noCookie) {
        // eslint-disable-next-line no-console
        console.warn(
          `[api-client] ${path} returned 401 with no auth credentials. ` +
          `Either silentRefresh() did not run, or the dev-trust cookie ` +
          `was rejected. See apps/admin/utils/auth.ts.`,
        );
      }
    }
    if (!res.ok) {
      let detail = `API ${res.status}: ${res.statusText}`;
      try {
        const body = await res.json();
        if (body.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
      } catch { /* no json body */ }
      throw new Error(detail);
    }
    return res.json();
  }

  return {
    get: <T>(path: string) => fetchJson<T>(path),
    post: <T>(path: string, body: unknown) =>
      fetchJson<T>(path, {
        method: 'POST',
        body: body instanceof FormData ? body : JSON.stringify(body),
      }),
    put: <T>(path: string, body: unknown) =>
      fetchJson<T>(path, {
        method: 'PUT',
        body: body instanceof FormData ? body : JSON.stringify(body),
      }),
    patch: <T>(path: string, body: unknown) =>
      fetchJson<T>(path, {
        method: 'PATCH',
        body: body instanceof FormData ? body : JSON.stringify(body),
      }),
    delete: <T>(path: string) => fetchJson<T>(path, { method: 'DELETE' }),
    raw: fetchJson,
  };
}

export type FetchClient = ReturnType<typeof createFetchClient>;

/** Create a WebSocket connection from an HTTP base URL. */
export function createChatWebSocket(baseUrl: string): WebSocket {
  const wsUrl = baseUrl.replace(/^http/, 'ws').replace(/\/api\/v1\/?$/, '');
  return new WebSocket(`${wsUrl}/ws/chat`);
}
