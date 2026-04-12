'use client';

import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';

export interface ConsentCategories {
  essential: true;
  analytics: boolean;
  marketing: boolean;
  preferences: boolean;
}

export interface ConsentState {
  version: number;
  timestamp: string;
  categories: ConsentCategories;
}

interface CookieConsentContextValue {
  consent: ConsentState | null;
  hasConsented: boolean;
  loaded: boolean;
  acceptAll: () => void;
  rejectAll: () => void;
  updateConsent: (categories: Partial<Omit<ConsentCategories, 'essential'>>) => void;
  resetConsent: () => void;
}

const STORAGE_KEY = 'sagewai-cookie-consent';
const CONSENT_VERSION = 1;

const CookieConsentContext = createContext<CookieConsentContextValue | null>(null);

function readConsent(): ConsentState | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ConsentState;
    if (parsed.version === CONSENT_VERSION && parsed.categories) return parsed;
    return null;
  } catch {
    return null;
  }
}

function writeConsent(state: ConsentState) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
}

export function CookieConsentProvider({ children }: { children: ReactNode }) {
  const [consent, setConsent] = useState<ConsentState | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setConsent(readConsent());
    setLoaded(true);
  }, []);

  const save = useCallback((categories: ConsentCategories) => {
    const state: ConsentState = {
      version: CONSENT_VERSION,
      timestamp: new Date().toISOString(),
      categories,
    };
    writeConsent(state);
    setConsent(state);
  }, []);

  const acceptAll = useCallback(() => {
    save({ essential: true, analytics: true, marketing: true, preferences: true });
  }, [save]);

  const rejectAll = useCallback(() => {
    save({ essential: true, analytics: false, marketing: false, preferences: false });
  }, [save]);

  const updateConsent = useCallback(
    (partial: Partial<Omit<ConsentCategories, 'essential'>>) => {
      const current = consent?.categories ?? {
        essential: true as const,
        analytics: false,
        marketing: false,
        preferences: false,
      };
      save({ ...current, ...partial, essential: true });
    },
    [consent, save],
  );

  const resetConsent = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY);
    setConsent(null);
  }, []);

  return (
    <CookieConsentContext.Provider
      value={{
        consent,
        hasConsented: consent !== null,
        loaded,
        acceptAll,
        rejectAll,
        updateConsent,
        resetConsent,
      }}
    >
      {children}
    </CookieConsentContext.Provider>
  );
}

export function useCookieConsent() {
  const ctx = useContext(CookieConsentContext);
  if (!ctx) throw new Error('useCookieConsent must be used within CookieConsentProvider');
  return ctx;
}
