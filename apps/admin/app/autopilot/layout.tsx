import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { AutopilotNavTabs } from '@/components/autopilot-nav';
import './focus.css';

export const metadata: Metadata = {
  title: 'Autopilot | Sagewai',
};

export default function AutopilotLayout({ children }: { children: ReactNode }) {
  return (
    <div className="max-w-4xl mx-auto autopilot-root">
      <AutopilotNavTabs />
      {children}
    </div>
  );
}
