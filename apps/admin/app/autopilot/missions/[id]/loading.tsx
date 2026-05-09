export default function Loading() {
  return (
    <div className="p-6 space-y-4" data-testid="mission-detail-loading">
      <div className="h-8 w-1/3 rounded bg-bg-subtle animate-pulse" />
      <div className="h-[480px] w-full rounded bg-bg-subtle animate-pulse" />
      <div className="h-32 w-full rounded bg-bg-subtle animate-pulse" />
    </div>
  );
}
