'use client';

import { Card } from '@/components/ui/legacy';
import { Lock } from 'lucide-react';

export function PremiumUpgradeCTA({ feature }: { feature: string }) {
  return (
    <Card>
      <div className="flex flex-col items-center justify-center py-16 text-center">
        <Lock className="w-12 h-12 text-text-muted mb-4" />
        <h3 className="text-lg font-semibold mb-2">{feature}</h3>
        <p className="text-sm text-text-secondary mb-4 max-w-[24rem]">
          This premium feature is available with a Sagewai license.
        </p>
        <a
          href="https://sagewai.com/pricing"
          target="_blank"
          rel="noopener noreferrer"
          className="px-4 py-2 text-sm font-medium rounded bg-primary text-white hover:opacity-90"
        >
          Upgrade to Premium
        </a>
      </div>
    </Card>
  );
}
