'use client';

import { useEffect, useState } from 'react';
import { Card } from '@/components/ui/legacy';
import { useLicense } from '@/utils/license';
import { PremiumUpgradeCTA } from '@/components/premium-upgrade-cta';
import { adminApi } from '@/utils/api';
import { HeatmapControls } from '@/components/premium/heatmap-controls';
import dynamic from 'next/dynamic';

const PerformanceHeatmap = dynamic(
  () =>
    import('@/components/premium/performance-heatmap').then((m) => m.PerformanceHeatmap),
  { loading: () => <p className="text-sm text-text-muted p-md">Loading heatmap...</p> },
);

const HeatmapTrend = dynamic(
  () => import('@/components/premium/heatmap-trend').then((m) => m.HeatmapTrend),
  { loading: () => <p className="text-sm text-text-muted p-md">Loading trend...</p> },
);

interface HeatmapDataPoint {
  workflow_name: string;
  date: string;
  total_runs: number;
  passed: number;
  failed: number;
  avg_duration_ms: number;
  p95_duration_ms: number;
}

export default function PerformancePage() {
  const license = useLicense();
  const [data, setData] = useState<HeatmapDataPoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(90);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    if (!license.isPremium && !license.loading) {
      setLoading(false);
      return;
    }

    async function load() {
      setLoading(true);
      try {
        const result = await adminApi.getWorkflowHeatmap(days);
        setData(result.data);
      } catch {
        // fallback to empty
      } finally {
        setLoading(false);
      }
    }

    load();
  }, [days, license.isPremium, license.loading]);

  const filteredData = filter
    ? data.filter((d) => d.workflow_name.toLowerCase().includes(filter.toLowerCase()))
    : data;

  if (!license.isPremium && !license.loading) {
    return (
      <div className="max-w-5xl mx-auto">
        <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-lg">
          Performance
        </h1>
        <PremiumUpgradeCTA feature="Workflow Performance Heatmap" />
      </div>
    );
  }

  // Summary stats
  const totalRuns = filteredData.reduce((s, d) => s + d.total_runs, 0);
  const totalPassed = filteredData.reduce((s, d) => s + d.passed, 0);
  const totalFailed = filteredData.reduce((s, d) => s + d.failed, 0);
  const overallRate = totalRuns > 0 ? ((totalPassed / totalRuns) * 100).toFixed(1) : '\u2014';

  return (
    <div className="max-w-6xl mx-auto">
      <h1 className="text-2xl font-bold font-[family-name:var(--font-heading)] mb-md">
        Performance
      </h1>
      <p className="text-sm text-text-muted mb-lg">
        Workflow success rates over time. Cell color reflects success rate, opacity reflects
        run volume. Click any cell to see failed runs.
      </p>

      <HeatmapControls
        days={days}
        onDaysChange={setDays}
        filter={filter}
        onFilterChange={setFilter}
      />

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-md mb-lg">
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Total Runs</div>
          <div className="text-lg font-semibold mt-1">{totalRuns.toLocaleString()}</div>
        </Card>
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Passed</div>
          <div className="text-lg font-semibold mt-1 text-success">{totalPassed.toLocaleString()}</div>
        </Card>
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Failed</div>
          <div className="text-lg font-semibold mt-1 text-error">{totalFailed.toLocaleString()}</div>
        </Card>
        <Card className="!p-md">
          <div className="text-xs text-text-muted uppercase">Success Rate</div>
          <div className="text-lg font-semibold mt-1">{overallRate}%</div>
        </Card>
      </div>

      {loading ? (
        <Card className="!p-8">
          <div className="text-sm text-text-muted text-center">Loading heatmap data...</div>
        </Card>
      ) : (
        <>
          <Card className="!p-4 mb-lg">
            <h3 className="text-sm font-semibold text-text-muted uppercase mb-3">
              Success Rate Heatmap
            </h3>
            <PerformanceHeatmap data={filteredData} days={days} />

            {/* Legend */}
            <div className="flex items-center gap-4 mt-3 text-[10px] text-text-muted">
              <span className="font-semibold uppercase">Success Rate:</span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-sm inline-block"
                  style={{ background: '#22c55e' }}
                />
                100%
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-sm inline-block"
                  style={{ background: '#eab308' }}
                />
                75%
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-sm inline-block"
                  style={{ background: '#f97316' }}
                />
                50%
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="w-3 h-3 rounded-sm inline-block"
                  style={{ background: '#ef4444' }}
                />
                &lt;50%
              </span>
              <span className="ml-4 font-semibold uppercase">Opacity = run volume</span>
            </div>
          </Card>

          <Card className="!p-4">
            <h3 className="text-sm font-semibold text-text-muted uppercase mb-3">
              Duration Trend
            </h3>
            <HeatmapTrend data={filteredData} />
          </Card>
        </>
      )}
    </div>
  );
}
