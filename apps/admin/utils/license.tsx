'use client';

import { createContext, useContext, type ReactNode } from 'react';

// Sagewai is AGPL-3.0 open source — every feature is delivered to every
// user. The LicenseProvider used to fetch /license and gate visualizations
// like Cost Flow, Canvas, Replay, TV Dashboard, and Analytics heatmaps.
// Now it always reports the full feature set so the UI renders everything.
interface LicenseState {
  isPremium: boolean;
  tier: string;
  features: string[];
  loading: boolean;
  error: boolean;
}

const OPEN_SOURCE_STATE: LicenseState = {
  isPremium: true,
  tier: 'agpl',
  features: [],
  loading: false,
  error: false,
};

const LicenseContext = createContext<LicenseState>(OPEN_SOURCE_STATE);

export function LicenseProvider({ children }: { children: ReactNode }) {
  return <LicenseContext.Provider value={OPEN_SOURCE_STATE}>{children}</LicenseContext.Provider>;
}

export function useLicense() {
  return useContext(LicenseContext);
}
