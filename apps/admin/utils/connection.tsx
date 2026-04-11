'use client';

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

type ConnectionState = 'connected' | 'disconnected' | 'checking';

interface ConnectionStatus {
  state: ConnectionState;
  /** True when the backend has never been reachable in this session. */
  neverConnected: boolean;
  /** Retry the health check immediately. */
  retry: () => void;
}

const ConnectionContext = createContext<ConnectionStatus>({
  state: 'checking',
  neverConnected: true,
  retry: () => {},
});

const API_BASE = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? process.env.NEXT_PUBLIC_ADMIN_API_URL.replace(/\/admin$/, '')
  : 'http://localhost:8000';

/** How often to re-check when disconnected (ms). */
const RETRY_INTERVAL = 8_000;
/** How often to re-check when connected (ms). */
const HEALTHY_INTERVAL = 30_000;

export function ConnectionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ConnectionState>('checking');
  const [neverConnected, setNeverConnected] = useState(true);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  const check = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/health/summary`, {
        signal: AbortSignal.timeout(5_000),
        cache: 'no-store',
      });
      // Any HTTP response (even 401/403/500) means the server is reachable.
      // Only network errors (catch block) indicate the server is truly down.
      if (res.status > 0) {
        setState('connected');
        setNeverConnected(false);
        return true;
      }
    } catch {
      // network error or timeout — server is unreachable
    }
    setState('disconnected');
    return false;
  }, []);

  const retry = useCallback(() => {
    setState('checking');
    clearTimeout(timerRef.current);
    check().then((ok) => {
      timerRef.current = setTimeout(check, ok ? HEALTHY_INTERVAL : RETRY_INTERVAL);
    });
  }, [check]);

  useEffect(() => {
    let active = true;

    const poll = async () => {
      if (!active) return;
      const ok = await check();
      if (!active) return;
      timerRef.current = setTimeout(poll, ok ? HEALTHY_INTERVAL : RETRY_INTERVAL);
    };

    poll();
    return () => {
      active = false;
      clearTimeout(timerRef.current);
    };
  }, [check]);

  return (
    <ConnectionContext.Provider value={{ state, neverConnected, retry }}>
      {children}
    </ConnectionContext.Provider>
  );
}

export function useConnection() {
  return useContext(ConnectionContext);
}
