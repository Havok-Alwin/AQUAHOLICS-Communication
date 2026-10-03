// DEV ONLY. Loaded only when MOCK is true (see main.ts), so it is not in a production build.
// Feeds link B messages shaped like the real backend's: FAST attitude at ~20 Hz on change,
// SLOW status at 2 Hz, with arrival jitter.
import { VEHICLES, type VehicleId } from './config';
import type { CurrentStateFields } from './currentState';
import { vehicleBackend, vehicles } from './sources';
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

const HOME: Record<VehicleId, { lat: number; lng: number }> = {
  USV1: { lat: 1.2966, lng: 103.7764 },
  UAV1: { lat: 1.2969, lng: 103.7768 },
};

// UAV1 drops out for 6 s every 30 s, so STALE / OFFLINE handling can be reviewed.
const dropped = (id: VehicleId, tMs: number) => id === 'UAV1' && tMs % 30000 > 24000;

function statusAt(id: VehicleId, tMs: number): Partial<CurrentStateFields> {
  const s = tMs / 1000;
  const uav = id === 'UAV1';
  const a = attitudeAt(id, tMs);
  return {
    armed: true,
    mode: uav ? 'GUIDED' : 'AUTO',
    failsafe: false,
    safetyactive: false,
    prearmstatus: true,
    ekfstatus: 0.1 + 0.05 * Math.sin(s / 7),
    landed_state: uav ? 2 : 0,
    groundspeed: (uav ? 4 : 1.5) + 0.3 * Math.sin(s / 3),
    airspeed: uav ? 4.2 : 0,
    verticalspeed: uav ? 0.2 * Math.sin(s / 4) : 0,
    groundcourse: a.y,
    turnrate: MOTION[id].yawRate,
    lat: HOME[id].lat + 0.0002 * Math.sin(s / 40),
    lng: HOME[id].lng + 0.0002 * Math.cos(s / 40),
    alt: uav ? 15 + Math.sin(s / 5) : 0.3,
    HomeAlt: 0,
    DistToHome: 22,
    wpno: 3,
    wp_dist: 40 + 10 * Math.sin(s / 9),
    nav_bearing: (a.y + 10) % 360,
    nav_roll: a.r * 0.9,
    nav_pitch: a.p * 0.9,
    targetalt: uav ? 15 : 0,
    targetairspeed: uav ? 4 : 1.5,
    xtrack_error: 0.4 * Math.sin(s / 6),
    battery_voltage: (uav ? 22.6 : 14.9) - s * 0.0005,
    battery_remaining: Math.max(0, 87 - Math.floor(s / 60)),
    current: uav ? 18 + 2 * Math.sin(s) : 6,
    gpsstatus: 3,
    satcount: 14,
    gpshdop: 0.8,
    linkqualitygcs: 100,
    load: 35,
    vibex: 3,
    vibey: 3.5,
    vibez: 5,
    messageHigh: 'MOCK: no real vehicle',
    messageHighSeverity: 6,
  };
}

export function startMock(): () => void {
  // Real per-vehicle link state comes from the backend's connect / link-lost logic (logic 8).
  vehicleBackend.setConnected(true);
  for (const id of VEHICLES) vehicles[id].setConnected(true);
  const last = new Map<VehicleId, string>();
  const timers: ReturnType<typeof setTimeout>[] = [];

  const later = (fn: () => void) => timers.push(setTimeout(fn, Math.random() * JITTER_MS));

  const fast = setInterval(() => {
    const t = performance.now();
    for (const id of VEHICLES) {
      if (dropped(id, t)) continue;
      const a = attitudeAt(id, t);
      const key = `${a.r},${a.p},${a.y}`;
      if (key === last.get(id)) continue; // pushed on change only, like the backend
      last.set(id, key);
      later(() => handleLinkB({ ch: 'att', vehicle: id, t, ...a }));
    }
  }, 1000 / FAST_HZ);

  const slow = setInterval(() => {
    const t = performance.now();
    for (const id of VEHICLES) {
      if (dropped(id, t)) continue;
      const cs = statusAt(id, t);
      later(() => handleLinkB({ ch: 'status', vehicle: id, t, cs }));
    }
  }, 1000 / SLOW_HZ);

  return () => {
    clearInterval(fast);
    clearInterval(slow);
    timers.forEach(clearTimeout);
    vehicleBackend.setConnected(false);
    for (const id of VEHICLES) vehicles[id].setConnected(false);
  };
}
