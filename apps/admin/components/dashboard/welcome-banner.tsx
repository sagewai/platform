'use client';

import { useEffect, useState } from 'react';

const LS_KEY = 'sagewai-welcome-visits';
const MAX_VISITS = 5;

export function WelcomeBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    try {
      const visits = parseInt(localStorage.getItem(LS_KEY) || '0', 10);
      if (visits < MAX_VISITS) {
        setVisible(true);
        localStorage.setItem(LS_KEY, String(visits + 1));
      }
    } catch {}
  }, []);

  if (!visible) return null;

  return (
    <div className="bg-gradient-to-r from-primary/10 via-primary/5 to-transparent border border-primary/20 rounded-xl p-6 mb-6">
      <h2 className="text-xl font-bold font-[family-name:var(--font-heading)] mb-1">
        Welcome to Sagewai
      </h2>
      <p className="text-text-secondary text-sm m-0">
        Your AI agent infrastructure is ready. Start building, training, and deploying intelligent agents.
      </p>
    </div>
  );
}
