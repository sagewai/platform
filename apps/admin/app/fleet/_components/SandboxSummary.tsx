"use client";

import { useState } from "react";

type WorkerLabels = Record<string, string | undefined>;

interface Props {
  labels: WorkerLabels;
}

const MODE_COLORS: Record<string, string> = {
  per_worker: "bg-emerald-500/10 text-emerald-700 border-emerald-500/20",
  per_run: "bg-emerald-500/10 text-emerald-700 border-emerald-500/20",
  per_tool: "bg-amber-500/10 text-amber-700 border-amber-500/20",
  none: "bg-rose-500/10 text-rose-700 border-rose-500/20",
};

export function SandboxSummary({ labels }: Props) {
  const [expanded, setExpanded] = useState(false);

  const mode = labels["sandbox.mode"] ?? "none";
  const backend = labels["sandbox.backend"] ?? "null";
  const variantsCsv = labels["sandbox.image_variants"] ?? "";
  const variants = variantsCsv ? variantsCsv.split(",").filter(Boolean) : [];
  const networkPolicy = labels["sandbox.network_policy"] ?? "none";

  const modeClass = MODE_COLORS[mode] ?? MODE_COLORS.none;
  const variantsLabel =
    variants.length === 0
      ? "[none]"
      : variants.length === 1
      ? `[${variants[0]}]`
      : `[${variants[0]} +${variants.length - 1}]`;

  return (
    <div
      onClick={() => setExpanded((v) => !v)}
      className="cursor-pointer inline-flex flex-col gap-1"
      aria-label="Sandbox configuration — click to expand"
    >
      <div className="flex items-center gap-1">
        <span
          className={`inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium ${modeClass}`}
        >
          {mode}
        </span>
        <span
          className="inline-flex items-center rounded border border-neutral-300 bg-neutral-100 px-1.5 py-0.5 text-xs text-neutral-700"
          title={variants.length > 0 ? variants.join(", ") : "no variants advertised"}
        >
          {variantsLabel}
        </span>
        <span className="inline-flex items-center rounded border border-neutral-300 bg-neutral-50 px-1.5 py-0.5 text-xs text-neutral-600">
          net:{networkPolicy}
        </span>
      </div>
      {expanded && (
        <div className="mt-1 rounded border border-neutral-200 bg-neutral-50 p-2 text-xs font-mono text-neutral-700">
          <div>
            <span className="text-neutral-500">Mode:</span> {mode}
          </div>
          <div>
            <span className="text-neutral-500">Backend:</span> {backend}
          </div>
          <div>
            <span className="text-neutral-500">Variants:</span>{" "}
            {variants.length > 0 ? variants.join(", ") : "(none)"}
          </div>
          <div>
            <span className="text-neutral-500">Network policy:</span> {networkPolicy}
          </div>
        </div>
      )}
    </div>
  );
}

export function UnsandboxedBadge({ labels }: Props) {
  const mode = labels["sandbox.mode"] ?? null;
  if (mode && mode !== "none" && mode !== "per_tool") return null;
  return (
    <span
      className="ml-2 inline-flex items-center rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700"
      title="Unsandboxed worker. This worker runs tools with no run-scoped isolation. Suitable for local development; not recommended for multi-tenant pools."
    >
      ⚠ Unsandboxed
    </span>
  );
}
