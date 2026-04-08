'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useRole } from '@/hooks/use-role';
import { useEffect } from 'react';

const TABS = [
  { href: '/training/logs', label: 'Run Logs' },
  { href: '/training/corpus', label: 'Corpus Builder' },
  { href: '/training/evals', label: 'Evaluations' },
  { href: '/training/finetune', label: 'Fine-Tuning Jobs' },
];

export default function TrainingLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { permissions } = useRole();

  useEffect(() => {
    if (!permissions.canTrain && !permissions.canManageSystem) {
      router.replace('/');
    }
  }, [permissions, router]);

  return (
    <div className="p-6 max-w-6xl">
      <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-1">Training</h1>
      <p className="text-text-secondary text-sm mb-6">Build training corpora, run evaluations, and fine-tune models</p>

      <div className="flex gap-1 border-b border-white/10 mb-6">
        {TABS.map((tab) => (
          <Link
            key={tab.href}
            href={tab.href}
            className={`px-4 py-2.5 text-sm no-underline transition-colors border-b-2 -mb-px ${
              pathname === tab.href
                ? 'border-primary text-white font-semibold'
                : 'border-transparent text-text-secondary hover:text-white'
            }`}
          >
            {tab.label}
          </Link>
        ))}
      </div>

      {children}
    </div>
  );
}
