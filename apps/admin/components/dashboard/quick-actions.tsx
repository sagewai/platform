'use client';

import Link from 'next/link';
import { Bot, Settings2, Play, BarChart2, GraduationCap, FileBarChart } from 'lucide-react';
import type { UserRole } from '@/utils/roles';

interface QuickAction {
  label: string;
  href: string;
  icon: React.ElementType;
  description: string;
}

const ROLE_ACTIONS: Record<UserRole, QuickAction[]> = {
  admin: [
    { label: 'Create Agent', href: '/agents', icon: Bot, description: 'Register a new AI agent' },
    { label: 'System Settings', href: '/system/organization', icon: Settings2, description: 'Configure your instance' },
    { label: 'Run Evaluation', href: '/eval/run', icon: BarChart2, description: 'Test agent quality' },
    { label: 'Playground', href: '/playground', icon: Play, description: 'Try an agent conversation' },
  ],
  developer: [
    { label: 'Create Agent', href: '/agents', icon: Bot, description: 'Register a new AI agent' },
    { label: 'Playground', href: '/playground', icon: Play, description: 'Try an agent conversation' },
    { label: 'MCP Servers', href: '/tools/mcp', icon: Settings2, description: 'Manage MCP tools' },
    { label: 'Run Evaluation', href: '/eval/run', icon: BarChart2, description: 'Test agent quality' },
  ],
  ml_engineer: [
    { label: 'Run Logs', href: '/training/logs', icon: GraduationCap, description: 'Browse agent conversations' },
    { label: 'Build Corpus', href: '/training/corpus', icon: FileBarChart, description: 'Create training datasets' },
    { label: 'Run Eval', href: '/training/evals', icon: BarChart2, description: 'Evaluate model quality' },
    { label: 'Fine-Tune', href: '/training/finetune', icon: GraduationCap, description: 'Start a fine-tuning job' },
  ],
  viewer: [
    { label: 'Cost Report', href: '/analytics/costs', icon: BarChart2, description: 'View spending trends' },
    { label: 'Agent Performance', href: '/analytics/performance', icon: FileBarChart, description: 'See agent success rates' },
    { label: 'Model Comparison', href: '/analytics/models', icon: BarChart2, description: 'Compare model metrics' },
  ],
};

export function QuickActions({ role }: { role: UserRole }) {
  const actions = ROLE_ACTIONS[role];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
      {actions.map((action) => {
        const Icon = action.icon;
        return (
          <Link
            key={action.href}
            href={action.href}
            className="bg-bg-surface border border-white/10 rounded-xl p-4 no-underline transition-all hover:border-primary/30 hover:bg-white/[0.03] group"
          >
            <Icon size={20} className="text-primary mb-2" />
            <div className="text-sm font-medium text-white group-hover:text-primary transition-colors">{action.label}</div>
            <div className="text-xs text-text-muted mt-0.5">{action.description}</div>
          </Link>
        );
      })}
    </div>
  );
}
