'use client';

import { useEffect, useRef } from 'react';

export type MissionStatusEvent = {
  mission_id: string;
  old_status: string;
  new_status: string;
  ts: string;
};

/**
 * Subscribe to the org-wide mission status SSE stream.
 *
 * One EventSource covers all missions. The browser auto-reconnects on
 * disconnect. Pass a stable `onEvent` callback (e.g. wrapped in useCallback).
 */
export function useMissionEvents(onEvent: (e: MissionStatusEvent) => void) {
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    const es = new EventSource('/api/v1/autopilot/missions/events');
    es.addEventListener('mission.status_changed', (msg) => {
      try {
        onEventRef.current(JSON.parse((msg as MessageEvent).data));
      } catch {
        // malformed event — ignore
      }
    });
    es.onerror = () => {
      // browser handles reconnect with exponential backoff automatically
    };
    return () => es.close();
  }, []);
}
