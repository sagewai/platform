'use client';

import { useEffect, useState } from 'react';
import { Card, Button, Skeleton, EmptyState } from '@/components/ui/legacy';
import { Play, Upload, TrendingUp } from 'lucide-react';

interface EvalResult {
  id: string;
  dataset: string;
  model: string;
  score: number;
  total: number;
  passed: number;
  failed: number;
  created_at: string;
}

export default function EvalsPage() {
  const [results, setResults] = useState<EvalResult[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setTimeout(() => { setResults([]); setLoading(false); }, 500);
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <p className="flex-1 text-text-secondary text-sm m-0">
          Run evaluations against models and track quality over time.
        </p>
        <Button><Upload size={14} className="mr-1.5" />Upload Dataset</Button>
        <Button><Play size={14} className="mr-1.5" />Run Eval</Button>
      </div>

      <Card>
        {loading ? (
          <div className="space-y-3 p-4">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : results.length === 0 ? (
          <EmptyState
            title="No evaluations yet"
            description="Upload an evaluation dataset (JSONL) and run it against any model to track quality."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left p-3 font-medium">Dataset</th>
                  <th className="text-left p-3 font-medium">Model</th>
                  <th className="text-right p-3 font-medium">Score</th>
                  <th className="text-right p-3 font-medium">Passed</th>
                  <th className="text-right p-3 font-medium">Failed</th>
                  <th className="text-left p-3 font-medium">Date</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r) => (
                  <tr key={r.id} className="border-b border-border hover:bg-primary/5 dark:hover:bg-white/[0.02]">
                    <td className="p-3 font-medium">{r.dataset}</td>
                    <td className="p-3 text-text-secondary">{r.model}</td>
                    <td className="p-3 text-right">
                      <span className={`font-semibold ${r.score >= 0.8 ? 'text-success' : r.score >= 0.5 ? 'text-warning' : 'text-error'}`}>
                        {(r.score * 100).toFixed(1)}%
                      </span>
                    </td>
                    <td className="p-3 text-right text-success">{r.passed}</td>
                    <td className="p-3 text-right text-error">{r.failed}</td>
                    <td className="p-3 text-text-secondary">{new Date(r.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
