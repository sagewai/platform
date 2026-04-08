'use client';

import { driver, type DriveStep } from 'driver.js';
import 'driver.js/dist/driver.css';

const TOURS: Record<string, DriveStep[]> = {
  welcome: [
    {
      element: '[data-tour="dashboard-kpis"]',
      popover: {
        title: 'Your Dashboard',
        description:
          'Key metrics at a glance \u2014 active agents, workflow runs, and costs.',
      },
    },
    {
      element: '[data-tour="dashboard-health"]',
      popover: {
        title: 'System Health',
        description:
          'Check your API keys, database connections, and worker status.',
      },
    },
    {
      element: '[data-tour="dashboard-actions"]',
      popover: {
        title: 'Quick Actions',
        description:
          'Jump to common operations: dispatch workflows, review approvals, check failed workflows.',
      },
    },
    {
      element: '[data-tour="nav-agents"]',
      popover: {
        title: 'Agent Registry',
        description: 'Register and manage your AI agents here.',
      },
    },
    {
      element: '[data-tour="nav-workflows"]',
      popover: {
        title: 'Workflows',
        description: 'Build, run, and monitor multi-agent workflows.',
      },
    },
  ],
  'workflow-builder': [
    {
      element: '[data-tour="workflow-stats"]',
      popover: {
        title: 'Queue Overview',
        description:
          'See how many workflows are pending, running, or failed. Click any card to filter history.',
      },
    },
    {
      element: '[data-tour="workflow-editor"]',
      popover: {
        title: 'Workflow Editor',
        description:
          'Write your workflow in YAML. Define agents, steps, and conditions.',
      },
    },
  ],
  'workflow-ops': [
    {
      element: '[data-tour="workers-table"]',
      popover: {
        title: 'Active Workers',
        description:
          'Workers poll the queue and execute workflows. Monitor their health here.',
      },
    },
  ],
  playground: [
    {
      element: '[data-tour="playground-model"]',
      popover: {
        title: 'Model Selection',
        description:
          'Choose which LLM to use. Supports GPT-4o, Claude, Gemini, and more.',
      },
    },
    {
      element: '[data-tour="playground-chat"]',
      popover: {
        title: 'Chat Interface',
        description:
          'Send messages and watch the agent reason step-by-step.',
      },
    },
  ],
};

export function startTour(tourId: string) {
  const steps = TOURS[tourId];
  if (!steps) return;

  const d = driver({
    showProgress: true,
    animate: true,
    overlayColor: 'rgba(10, 22, 40, 0.7)',
    steps,
    onDestroyed: () => {
      localStorage.setItem(`tour:${tourId}`, 'done');
    },
  });
  d.drive();
}

export function shouldShowTour(tourId: string): boolean {
  if (typeof window === 'undefined') return false;
  return !localStorage.getItem(`tour:${tourId}`);
}
