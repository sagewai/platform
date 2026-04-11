'use client';

import { useEffect, useState } from 'react';
import { Card, Button, Skeleton, EmptyState } from '@/components/ui/legacy';
import { ShieldCheck, Copy, AlertTriangle, BarChart2 } from 'lucide-react';

export default function DataQualityPage() {
  const [loading, setLoading] = useState(true);
  const [hasData, setHasData] = useState(false);

  useEffect(() => {
    setTimeout(() => { setLoading(false); }, 500);
  }, []);

  return (
    <div className="space-y-4">
      <p className="text-text-secondary text-sm m-0">
        Analyze training data quality: detect duplicates, scan for PII, and identify noise.
      </p>

      {/* Quality metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <div className="flex items-center gap-3">
            <Copy size={18} className="text-warning" />
            <div>
              <div className="text-xl font-bold">—</div>
              <div className="text-xs text-text-muted">Duplicates</div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center gap-3">
            <ShieldCheck size={18} className="text-error" />
            <div>
              <div className="text-xl font-bold">—</div>
              <div className="text-xs text-text-muted">PII Detected</div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center gap-3">
            <AlertTriangle size={18} className="text-warning" />
            <div>
              <div className="text-xl font-bold">—</div>
              <div className="text-xs text-text-muted">Noise Items</div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center gap-3">
            <BarChart2 size={18} className="text-success" />
            <div>
              <div className="text-xl font-bold">—</div>
              <div className="text-xs text-text-muted">Avg Quality</div>
            </div>
          </div>
        </Card>
      </div>

      {/* Analysis results */}
      <Card>
        {loading ? (
          <div className="space-y-3 p-4">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : !hasData ? (
          <EmptyState
            title="No quality analysis yet"
            description="Create a training corpus first, then run quality analysis to detect issues."
          />
        ) : null}
      </Card>

      <div className="flex gap-3">
        <Button disabled={!hasData}>Run Dedup Analysis</Button>
        <Button disabled={!hasData}>Scan for PII</Button>
        <Button disabled={!hasData}>Detect Noise</Button>
      </div>
    </div>
  );
}
