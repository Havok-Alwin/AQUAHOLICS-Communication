// Logic 0: attitude extrapolation and smoothing, ported from ~/Test_frontend/frontend/app.js.
// Plain mutable state, deliberately outside Svelte reactivity: it is read once per frame.

export interface AttitudeSample {
  /** Sender clock in ms (backend Stopwatch). Velocity uses this, not arrival time, to ignore network jitter. */
  t: number;
  roll: number;
  pitch: number;
  yaw: number;
}

export interface Attitude {
  roll: number;
  pitch: number;
  yaw: number;
}

const VEL_BLEND = 0.5; // blend new velocity estimate with the old one (noise filter)
const MIN_DT_S = 0.005; // samples closer than this give no usable velocity
const MAX_DT_S = 0.5; // a gap longer than this resets velocity to 0
const MAX_AHEAD_S = 0.15; // extrapolate at most 150 ms past the last sample, then hold
const EASE_TAU_S = 0.015; // light 15 ms ease: removes jitter, adds almost no lag

/** Shortest signed angle difference in degrees, in [-180, 180). */
export const wrap = (d: number): number => ((((d + 180) % 360) + 360) % 360) - 180;

export class AttitudeTrack {
  /** What is drawn this frame. */
  readonly shown: Attitude = { roll: 0, pitch: 0, yaw: 0 };
  /** Samples received since the last call to takePacketCount(). */
  private packets = 0;
  private hasSample = false;
  private last: AttitudeSample = { t: 0, roll: 0, pitch: 0, yaw: 0 };
  private lastArrivalMs = 0;
  private vel: Attitude = { roll: 0, pitch: 0, yaw: 0 };

  push(s: AttitudeSample, arrivalMs: number = performance.now()): void {
    const dt = (s.t - this.last.t) / 1000;
    if (this.hasSample && dt > MIN_DT_S && dt < MAX_DT_S) {
      this.vel.roll += VEL_BLEND * ((s.roll - this.last.roll) / dt - this.vel.roll);
      this.vel.pitch += VEL_BLEND * ((s.pitch - this.last.pitch) / dt - this.vel.pitch);
      this.vel.yaw += VEL_BLEND * (wrap(s.yaw - this.last.yaw) / dt - this.vel.yaw);
    } else {
      this.vel = { roll: 0, pitch: 0, yaw: 0 };
    }
    if (!this.hasSample) {
      // First sample: start from it instead of easing in from 0.
      this.shown.roll = s.roll;
      this.shown.pitch = s.pitch;
      this.shown.yaw = s.yaw;
      this.hasSample = true;
    }
    this.last = { ...s };
    this.lastArrivalMs = arrivalMs;
    this.packets++;
  }

  /** Advance `shown` to frame time `nowMs`. Call once per frame from the frame clock. */
  step(nowMs: number, dtS: number): void {
    if (!this.hasSample) return;
    const ahead = Math.min(Math.max((nowMs - this.lastArrivalMs) / 1000, 0), MAX_AHEAD_S);
    const goalRoll = this.last.roll + this.vel.roll * ahead;
    const goalPitch = this.last.pitch + this.vel.pitch * ahead;
    const goalYaw = this.last.yaw + this.vel.yaw * ahead;
    const a = 1 - Math.exp(-dtS / EASE_TAU_S);
    this.shown.roll += (goalRoll - this.shown.roll) * a;
    this.shown.pitch += (goalPitch - this.shown.pitch) * a;
    this.shown.yaw += wrap(goalYaw - this.shown.yaw) * a;
  }

  get received(): boolean {
    return this.hasSample;
  }

  takePacketCount(): number {
    const n = this.packets;
    this.packets = 0;
    return n;
  }
}
