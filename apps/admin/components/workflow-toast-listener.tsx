'use client';

import { useEffect, useRef } from 'react';
import { useToast } from '@sagecurator/ui';
import { authSSE } from '@/utils/auth';

const API_BASE = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? process.env.NEXT_PUBLIC_ADMIN_API_URL.replace(/\/admin$/, '')
  : 'http://localhost:8000';

export function WorkflowToastListener() {
  const { toast } = useToast();
  const toastRef = useRef(toast);
  toastRef.current = toast;

  useEffect(() => {
    const controller = authSSE(
      `${API_BASE}/workflow-events/stream`,
      (event, data) => {
        if (event === 'workflow_completed' || event === 'workflow_finished') {
          toastRef.current('success', `Workflow completed: ${data.workflow_name || 'finished successfully'}`);
        } else if (event === 'workflow_failed') {
          toastRef.current('error', `Workflow failed: ${data.error || data.workflow_name || 'unknown error'}`);
        } else if (event === 'approval_requested') {
          toastRef.current('info', `Approval needed: ${data.workflow_name || 'a workflow needs your review'}`);
        }
      },
      { reconnect: true },
    );

    return () => controller.abort();
  }, []);

  return null;
}
