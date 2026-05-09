'use client';

import { useState } from 'react';
import ReactMarkdown from 'react-markdown';

export function AutopilotMissionOutput({ output }: { output: unknown }) {
  const [copied, setCopied] = useState(false);

  const json = JSON.stringify(output, null, 2);
  const encoded = encodeURIComponent(json);
  const downloadHref = `data:application/json,${encoded}`;

  function handleCopy() {
    navigator.clipboard.writeText(json).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <section
      className="rounded-lg border border-border bg-bg-surface p-4 flex flex-col gap-3"
      data-testid="mission-output"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-text-primary m-0">
          Final output
        </h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleCopy}
            className="text-xs px-2 py-1 rounded border border-border text-text-secondary hover:bg-bg-subtle transition-colors"
          >
            {copied ? 'Copied' : 'Copy JSON'}
          </button>
          <a
            href={downloadHref}
            download="mission-output.json"
            className="text-xs px-2 py-1 rounded border border-border text-text-secondary hover:bg-bg-subtle transition-colors"
          >
            Download
          </a>
        </div>
      </div>

      <div className="rounded bg-bg-subtle p-3 overflow-auto max-h-96">
        {typeof output === 'string' ? (
          <div className="prose prose-sm max-w-none text-text-primary dark:prose-invert">
            <ReactMarkdown>{output}</ReactMarkdown>
          </div>
        ) : (
          <pre className="text-xs font-mono text-text-primary m-0 whitespace-pre-wrap break-all">
            {json}
          </pre>
        )}
      </div>
    </section>
  );
}
