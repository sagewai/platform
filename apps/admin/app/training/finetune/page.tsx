'use client';

import { useEffect, useState } from 'react';
import { Card, Button, Skeleton, EmptyState } from '@sagecurator/ui';
import { Plus, Cpu, Clock, TrendingDown } from 'lucide-react';

interface FineTuneJob {
  id: string;
  name: string;
  base_model: string;
  corpus: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  epoch: number;
  total_epochs: number;
  loss: number | null;
  created_at: string;
  eta: string | null;
}

export default function FineTunePage() {
  const [jobs, setJobs] = useState<FineTuneJob[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setTimeout(() => { setJobs([]); setLoading(false); }, 500);
  }, []);

  const statusColors: Record<string, string> = {
    queued: 'bg-white/10 text-text-secondary',
    running: 'bg-primary/20 text-primary',
    completed: 'bg-success/20 text-success',
    failed: 'bg-error/20 text-error',
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-text-secondary text-sm m-0">
          Fine-tune models with Unsloth. Configure LoRA adapters, train, and export to GGUF.
        </p>
        <Button><Plus size={14} className="mr-1.5" />New Job</Button>
      </div>

      {loading ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full rounded-xl" />
          <Skeleton className="h-24 w-full rounded-xl" />
        </div>
      ) : jobs.length === 0 ? (
        <Card>
          <EmptyState
            title="No fine-tuning jobs"
            description="Create a training corpus first, then start a fine-tuning job to customize a model for your domain."
          />
        </Card>
      ) : (
        <div className="space-y-3">
          {jobs.map((job) => (
            <Card key={job.id}>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h3 className="text-sm font-semibold m-0">{job.name}</h3>
                  <p className="text-xs text-text-muted m-0 mt-1">
                    Base: {job.base_model} · Corpus: {job.corpus}
                  </p>
                </div>
                <span className={`px-2.5 py-1 rounded-full text-xs font-medium ${statusColors[job.status]}`}>
                  {job.status}
                </span>
              </div>
              {job.status === 'running' && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between text-xs">
                    <span className="text-text-muted flex items-center gap-1"><Clock size={12} />Epoch {job.epoch}/{job.total_epochs}</span>
                    {job.loss !== null && <span className="text-text-muted flex items-center gap-1"><TrendingDown size={12} />Loss: {job.loss.toFixed(4)}</span>}
                    {job.eta && <span className="text-text-muted">ETA: {job.eta}</span>}
                  </div>
                  <div className="w-full bg-white/5 rounded-full h-2">
                    <div className="bg-primary h-2 rounded-full transition-all" style={{ width: `${(job.epoch / job.total_epochs) * 100}%` }} />
                  </div>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
