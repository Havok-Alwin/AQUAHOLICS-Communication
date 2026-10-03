// DEV ONLY. Loaded only when MOCK is true (see main.ts), so it is not in a production build.
// Feeds link B messages shaped like the real backend's: FAST attitude at ~20 Hz on change,
// SLOW status at 2 Hz, with arrival jitter.
import { VEHICLES, type VehicleId } from './config';
import { vehicleBackend } from './sources';
import { handleLinkB } from './telemetry';

const FAST_HZ = 20;
const SLOW_HZ = 2;
const JITTER_MS = 15;

// Gentle motion for the boat, larger for the drone.
const MOTION: Record<VehicleId, { rollAmp: number; pitchAmp: number; yawRate: number; f: number }> = {
  USV1: { rollAmp: 6, pitchAmp: 2.5, yawRate: 4, f: 0.25 },
  UAV1: { rollAmp: 20, pitchAmp: 10, yawRate: 15, f: 0.15 },
};

function attitudeAt(id: VehicleId, tMs: number) {
  const m = MOTION[id];
  const s = tMs / 1000;
  const round = (v: number) => Math.round(v * 100) / 100;
  return {
    r: round(m.rollAmp * Math.sin(2 * Math.PI * m.f * s)),
    p: round(m.pitchAmp * Math.sin(2 * Math.PI * m.f * 0.7 * s + 1)),
    y: round((((m.yawRate * s) % 360) + 360) % 360),
  };
}

export function startMock(): () => void {
  vehicleBackend.setConnected(true);
  const last = new Map<VehicleId, string>();
  const timers: ReturnType<typeof setTimeout>[] = [];

  const later = (fn: () => void) => timers.push(setTimeout(fn, Math.random() * JITTER_MS));

  const fast = setInterval(() => {
    const t = performance.now();
    for (const id of VEHICLES) {
      const a = attitudeAt(id, t);
      const key = `${a.r},${a.p},${a.y}`;
      if (key === last.get(id)) continue; // pushed on change only, like the backend
      last.set(id, key);
      later(() => handleLinkB({ ch: 'att', vehicle: id, t, ...a }));
    }
  }, 1000 / FAST_HZ);

  const slow = setInterval(() => {
    const t = performance.now();
    for (const id of VEHICLES) later(() => handleLinkB({ ch: 'status', vehicle: id, t }));
  }, 1000 / SLOW_HZ);

  return () => {
    clearInterval(fast);
    clearInterval(slow);
    timers.forEach(clearTimeout);
    vehicleBackend.setConnected(false);
  };
}
