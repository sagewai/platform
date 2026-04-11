'use client';

import { Button } from '@/components/ui/legacy';

const API_BASE = process.env.NEXT_PUBLIC_ADMIN_API_URL
  ? process.env.NEXT_PUBLIC_ADMIN_API_URL.replace(/\/admin$/, '')
  : 'http://localhost:8000';

interface ExportButtonProps {
  /** Label shown on the button */
  label?: string;
  /** Export format: json or csv */
  format: 'json' | 'csv';
  /** Additional query params to pass */
  params?: Record<string, string>;
}

export function ExportButton({
  label,
  format,
  params = {},
}: ExportButtonProps) {
  const handleExport = () => {
    const sp = new URLSearchParams({ format, ...params });
    const url = `${API_BASE}/api/v1/audit/export?${sp.toString()}`;
    window.open(url, '_blank');
  };

  return (
    <Button
      onClick={handleExport}
      variant={format === 'csv' ? 'secondary' : 'primary'}
    >
      {label ?? `Export ${format.toUpperCase()}`}
    </Button>
  );
}
