import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="p-6 flex flex-col gap-2" data-testid="mission-detail-not-found">
      <h1 className="text-lg font-semibold text-text-primary m-0">
        Mission not found
      </h1>
      <p className="text-text-secondary text-sm m-0">
        It may have been deleted or never existed.
      </p>
      <Link
        href="/autopilot/missions"
        className="text-primary hover:underline text-sm"
      >
        Back to Missions
      </Link>
    </div>
  );
}
