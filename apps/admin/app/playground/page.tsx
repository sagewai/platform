import { PlaygroundView } from '@/components/playground-view';
import { TourTrigger } from '@/components/tour-trigger';

export const dynamic = 'force-dynamic';

export default function PlaygroundPage() {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h1 className="mt-0 mb-0 text-2xl font-bold font-[family-name:var(--font-heading)]">Playground</h1>
        <TourTrigger tourId="playground" autoStart={false} />
      </div>
      <p className="mt-0 mb-lg text-sm text-text-secondary">
        Create agents, pick a strategy, and chat — watch AG-UI events in real time.
      </p>
      <div data-tour="playground-model">
        <div data-tour="playground-chat">
          <PlaygroundView />
        </div>
      </div>
    </div>
  );
}
