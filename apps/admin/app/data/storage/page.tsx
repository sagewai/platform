'use client';

import { useEffect, useState } from 'react';
import { Card, Button, Skeleton, EmptyState } from '@/components/ui/legacy';
import { Upload, FolderOpen, HardDrive } from 'lucide-react';

interface StorageBucket {
  id: string;
  name: string;
  type: 's3' | 'local' | 'gcs';
  files: number;
  size_gb: number;
  last_modified: string;
}

export default function StoragePage() {
  const [buckets, setBuckets] = useState<StorageBucket[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setTimeout(() => { setBuckets([]); setLoading(false); }, 500);
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-text-secondary text-sm m-0">
          Browse and manage S3/local archives for training corpora.
        </p>
        <Button><Upload size={14} className="mr-1.5" />Upload</Button>
      </div>

      {/* Storage overview */}
      <div className="grid grid-cols-3 gap-4">
        <Card>
          <div className="flex items-center gap-3">
            <HardDrive size={20} className="text-primary" />
            <div>
              <div className="text-2xl font-bold">0 GB</div>
              <div className="text-xs text-text-muted">Total Storage</div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center gap-3">
            <FolderOpen size={20} className="text-secondary" />
            <div>
              <div className="text-2xl font-bold">0</div>
              <div className="text-xs text-text-muted">Corpora Files</div>
            </div>
          </div>
        </Card>
        <Card>
          <div className="flex items-center gap-3">
            <Upload size={20} className="text-accent-purple" />
            <div>
              <div className="text-2xl font-bold">0</div>
              <div className="text-xs text-text-muted">Archives</div>
            </div>
          </div>
        </Card>
      </div>

      {/* Buckets list */}
      <Card>
        {loading ? (
          <div className="space-y-3 p-4">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : buckets.length === 0 ? (
          <EmptyState
            title="No storage configured"
            description="Configure S3, GCS, or local storage in System Settings to manage training data archives."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  <th className="text-left p-3 font-medium">Name</th>
                  <th className="text-left p-3 font-medium">Type</th>
                  <th className="text-right p-3 font-medium">Files</th>
                  <th className="text-right p-3 font-medium">Size</th>
                  <th className="text-left p-3 font-medium">Last Modified</th>
                </tr>
              </thead>
              <tbody>
                {buckets.map((b) => (
                  <tr key={b.id} className="border-b border-border hover:bg-primary/5 dark:hover:bg-white/[0.02]">
                    <td className="p-3 font-medium">{b.name}</td>
                    <td className="p-3"><span className="px-2 py-0.5 rounded bg-bg-subtle text-xs uppercase">{b.type}</span></td>
                    <td className="p-3 text-right">{b.files}</td>
                    <td className="p-3 text-right">{b.size_gb.toFixed(2)} GB</td>
                    <td className="p-3 text-text-secondary">{new Date(b.last_modified).toLocaleDateString()}</td>
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
