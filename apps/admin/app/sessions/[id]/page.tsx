import Link from 'next/link';

import { adminApi } from '@/utils/api';
import type { SessionMessagesResponse } from '@/utils/types';
import { Card, EmptyState } from '@sagecurator/ui';

export const dynamic = 'force-dynamic';

export default async function SessionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  let data: SessionMessagesResponse | null = null;
  let error = '';
  try {
    data = await adminApi.getSessionMessages(id);
  } catch {
    error = 'Failed to load session messages. The API may be unavailable.';
  }

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
