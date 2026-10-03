// Logic 0: one fixed frame clock for everything that animates.
// Drawing happens on requestAnimationFrame, never directly on packet arrival.

export type FrameCallback = (nowMs: number, dtS: number) => void;

const callbacks = new Set<FrameCallback>();
let rafId = 0;
let prevMs = 0;

function frame(nowMs: number): void {
  // Cap dt so a paused tab does not make the ease jump.
  const dtS = Math.min((nowMs - prevMs) / 1000, 0.1);
  prevMs = nowMs;
  for (const cb of callbacks) cb(nowMs, dtS);
  rafId = requestAnimationFrame(frame);
}

/** Run `cb` every frame until the returned function is called. */
export function onFrame(cb: FrameCallback): () => void {
  callbacks.add(cb);
  if (callbacks.size === 1) {
    prevMs = performance.now();
    rafId = requestAnimationFrame(frame);
  }
  return () => {
    callbacks.delete(cb);
    if (callbacks.size === 0) cancelAnimationFrame(rafId);
  };
}
