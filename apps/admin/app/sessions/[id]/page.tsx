'use client';

// Session messages are authenticated control-plane data, so this must run in the
// BROWSER, not as a Server Component. Server-side rendering happens inside the
// admin container, where (a) `localhost:8000` is the admin itself rather than the
// backend, and (b) there is no access to the user's bearer token (it lives in
// browser storage) — so the call fails/401 and the page reports the API as down.
// Fetching client-side reuses the same auth + host the other admin pages use.
// See app/page.tsx (PR #468) for the matching dashboard fix. The route param is
// read with useParams() because client components have no server `params` prop.

import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';

import { adminApi } from '@/utils/api';
import type { SessionMessagesResponse } from '@/utils/types';
import { Card, EmptyState } from '@/components/ui/legacy';

const roleBg: Record<string, string> = {
  user: 'bg-info-light',
  assistant: 'bg-success-light',
  system: 'bg-purple-50',
  tool: 'bg-warning-light',
};

const roleAlign: Record<string, string> = {
  user: 'justify-end',
  assistant: 'justify-start',
  system: 'justify-center',
  tool: 'justify-start',
};

export default function SessionDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<SessionMessagesResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData(await adminApi.getSessionMessages(id));
    } catch {
      setError('Failed to load session messages. The API may be unavailable.');
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="max-w-6xl mx-auto">
      <div className="mb-lg">
        <Link
          href="/sessions"
          className="text-primary no-underline text-sm font-medium hover:underline"
        >
          &larr; Back to Sessions
        </Link>
      </div>

      <h1 className="mt-0 mb-lg text-2xl font-bold font-[family-name:var(--font-heading)]">Session Detail</h1>

      {loading && (
        <div className="text-sm text-text-muted mb-md">Loading session…</div>
      )}

      {error && (
        <div className="bg-error-light border border-error/20 rounded-lg px-5 py-3 text-error text-sm mb-md">
          {error}
        </div>
      )}

      {data && (
        <>
          {/* Metadata cards */}
          <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-md mb-xl">
            <Card>
              <div className="text-xs text-text-muted mb-1">Session ID</div>
              <div className="font-[family-name:var(--font-mono)] text-[13px] break-all">
                {data.session_id}
              </div>
            </Card>

            <Card>
              <div className="text-xs text-text-muted mb-1">Agent</div>
              <div className="text-[15px] font-medium">{data.agent_name}</div>
            </Card>

            <Card>
              <div className="text-xs text-text-muted mb-1">Messages</div>
              <div className="text-xl font-semibold">{data.total_messages}</div>
            </Card>

            <Card>
              <div className="text-xs text-text-muted mb-1">Updated</div>
              <div className="text-[13px]">
                {data.updated_at ? new Date(data.updated_at).toLocaleString() : 'N/A'}
              </div>
            </Card>
          </div>

          {/* Chat timeline */}
          <h2 className="text-lg font-semibold mt-0 mb-md font-[family-name:var(--font-heading)]">Chat Timeline</h2>

          {data.messages.length === 0 ? (
            <EmptyState title="No Messages" description="No messages in this session." />
          ) : (
            <div className="flex flex-col gap-3">
              {data.messages.map((msg, idx) => {
                const role = msg.role.toLowerCase();
                const bg = roleBg[role] ?? 'bg-bg-subtle';
                const align = roleAlign[role] ?? 'justify-start';
                const isSystem = role === 'system';

                return (
                  <div key={idx} className={`flex ${align}`}>
                    <div
                      className={`${bg} ${isSystem ? 'rounded-md px-4 py-2 w-full text-center' : 'rounded-xl px-4 py-3 max-w-[70%]'}`}
                    >
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-text-muted mb-1">
                        {msg.role}
                      </div>

                      <div
                        className={`text-sm leading-relaxed whitespace-pre-wrap break-words ${isSystem ? 'text-text-muted' : 'text-text-primary'}`}
                      >
                        {msg.content}
                      </div>

                      {(msg.timestamp || msg.token_count) && (
                        <div className="flex gap-3 mt-1.5 text-[11px] text-text-muted">
                          {msg.timestamp && (
                            <span>{new Date(msg.timestamp).toLocaleString()}</span>
                          )}
                          {msg.token_count != null && msg.token_count > 0 && (
                            <span>{msg.token_count} tokens</span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
