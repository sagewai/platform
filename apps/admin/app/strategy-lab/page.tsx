import { StrategyComparison } from '@/components/strategy-comparison';

export const dynamic = 'force-dynamic';

export default function StrategyLabPage() {
  return (
    <div>
      <h1 className="mt-0 mb-1 text-2xl font-bold font-[family-name:var(--font-heading)]">Strategy Lab</h1>
      <p className="mt-0 mb-lg text-sm text-text-secondary max-w-3xl">
        Execution strategies control <em>how</em> an agent reasons — from a single fast LLM call to
        multi-round debates with self-critique. Use the guide below to learn which strategy fits
        your task, then compare them side-by-side with the same prompt.
      </p>
      <StrategyComparison />
    </div>
  );
}
