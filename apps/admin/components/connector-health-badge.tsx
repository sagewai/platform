'use client';

import { Badge } from '@sagecurator/ui';
import type { ConnectorHealthResult } from '@/utils/types';

interface ConnectorHealthBadgeProps {
  connected: boolean;
  health?: ConnectorHealthResult | null;
  showLatency?: boolean;
}

/**
 * Renders a health status badge for a connector.
 *
 * States:
 * - healthy (with latency) → green "Healthy (Xms)"
 * - degraded → amber "Degraded"
 * - disconnected/error → red "Error"
 * - connected, no health check → blue "Connected"
 * - not connected → gray "Not connected"
 */
export function ConnectorHealthBadge({
  connected,
  health,
  showLatency = true,
}: ConnectorHealthBadgeProps) {
  if (!health) {
    if (connected) return <Badge variant="info">Connected</Badge>;
    return <Badge variant="default">Not connected</Badge>;
  }

  switch (health.status) {
    case 'healthy':
      return (
        <Badge variant="success">
          Healthy{showLatency && health.latency_ms ? ` (${health.latency_ms}ms)` : ''}
        </Badge>
      );
    case 'degraded':
      return <Badge variant="warning">Degraded</Badge>;
    default:
      return <Badge variant="error">{health.error ? 'Error' : 'Disconnected'}</Badge>;
  }
}
