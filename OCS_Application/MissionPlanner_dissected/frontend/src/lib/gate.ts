import { UI_UPDATE_MIN_MS } from './pacing';

// Logic 2: update gate, ported from MissionPlanner FlightData.updateBindingSource (1.3.x IL):
//   - push to the UI at most every 100 ms;
//   - never queue a second UI update while one is pending (MP skips; we keep only the latest value);
// MP's 5 s watchdog (reset a stuck pending count) is not needed here: the pending timer always fires.
//
// Latest-value-wins: a burst (e.g. backend reconnect flushing) costs at most one UI update per
// interval, and the value shown is always the newest one, never an older queued one.

export interface Gate<T> {
  push(value: T): void;
  /** Drop a pending value (e.g. on disconnect) so nothing old is applied later. */
  cancel(): void;
}

export function createGate<T>(
  apply: (value: T) => void,
  minIntervalMs: number = UI_UPDATE_MIN_MS,
  clock: () => number = () => performance.now(),
): Gate<T> {
  let lastApply = -Infinity;
  let pending: { value: T } | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const flush = () => {
    timer = null;
    if (!pending) return;
    const { value } = pending;
    pending = null;
    lastApply = clock();
    apply(value);
  };

  return {
    push(value) {
      pending = { value };
      if (timer) return; // one update already scheduled: it will take this newer value
      const wait = lastApply + minIntervalMs - clock();
      if (wait <= 0) flush();
      else timer = setTimeout(flush, wait);
    },
    cancel() {
      pending = null;
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
}
