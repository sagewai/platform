'use client';

import { useEffect } from 'react';
import { HelpCircle } from 'lucide-react';
import { startTour, shouldShowTour } from '@/utils/tours';

interface TourTriggerProps {
  tourId: string;
  /** Auto-start the tour on first visit */
  autoStart?: boolean;
}

export function TourTrigger({ tourId, autoStart = true }: TourTriggerProps) {
  useEffect(() => {
    if (autoStart && shouldShowTour(tourId)) {
      // Small delay to let elements render before tour starts
      const timer = setTimeout(() => startTour(tourId), 600);
      return () => clearTimeout(timer);
    }
  }, [tourId, autoStart]);

  return (
    <button
      onClick={() => startTour(tourId)}
      className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded border border-border hover:bg-bg-subtle"
      title="Take a tour"
    >
      <HelpCircle className="w-3.5 h-3.5" />
      Tour
    </button>
  );
}
