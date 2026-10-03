import { derived, writable, type Readable } from 'svelte/store';
import { now } from './clock';

// Each data source (vehicle backend, each vehicle, OCS) fails independently.
// A value from a source may be shown as live only while its state is 'live'.
export type SourceState = 'connecting' | 'live' | 'stale' | 'offline';

export interface SourceStatus {
  state: SourceState;
  /** ms since the last update, or null if no update was ever received. */
  ageMs: number | null;
}

export interface Source {
  readonly name: string;
  readonly status: Readable<SourceStatus>;
  /** Transport is open (WebSocket / EventSource connected). */
  setConnected(connected: boolean): void;
  /** A fresh update was received from this source. */
  markUpdate(at?: number): void;
}

export function createSource(name: string, staleAfterMs: number): Source {
  const raw = writable({ connected: false, lastUpdate: null as number | null });

  const status = derived([raw, now], ([$raw, $now]): SourceStatus => {
    const ageMs = $raw.lastUpdate === null ? null : Math.max(0, $now - $raw.lastUpdate);
    let state: SourceState;
    if (!$raw.connected) state = 'offline';
    else if (ageMs === null) state = 'connecting';
    else if (ageMs > staleAfterMs) state = 'stale';
    else state = 'live';
    return { state, ageMs };
  });

  return {
    name,
    status,
    setConnected: (connected) => raw.update((r) => ({ ...r, connected })),
    markUpdate: (at = Date.now()) => raw.update((r) => ({ ...r, lastUpdate: at })),
  };
}

export function formatAge(ageMs: number | null): string {
  if (ageMs === null) return 'never';
  const s = ageMs / 1000;
  if (s < 60) return `${s.toFixed(1)} s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)} h ago`;
}
