'use client';

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { authFetch } from './auth';

interface LicenseState {
  isPremium: boolean;
  tier: string;
  features: string[];
  loading: boolean;
  error: boolean;
}

const LicenseContext = createContext<LicenseState>({
  isPremium: false,
  tier: 'free',
  features: [],
  loading: true,
  error: false,
});

const API_BASE = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? process.env.NEXT_PUBLIC_ADMIN_API_URL.replace(/\/admin$/, '')
  : 'http://localhost:8000';

export function LicenseProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<LicenseState>({
    isPremium: false,
    tier: 'free',
    features: [],
    loading: true,
    error: false,
  });

  useEffect(() => {
    authFetch(`${API_BASE}/license`)
      .then((r) => r.json())
      .then((data) =>
        setState({
          isPremium: data.tier === 'premium',
          tier: data.tier,
          features: data.features || [],
          loading: false,
          error: false,
        }),
      )
      .catch(() => setState((s) => ({ ...s, loading: false, error: true })));
  }, []);

  return <LicenseContext.Provider value={state}>{children}</LicenseContext.Provider>;
}

export function useLicense() {
  return useContext(LicenseContext);
}
