import { AttitudeTrack } from './attitude';
import { VEHICLES, type VehicleId } from './config';
import { vehicleBackend, vehicles } from './sources';

// Logic 0: link B carries two channels per vehicle.
//   FAST: roll/pitch/yaw, pushed only when the value changes (~20 Hz while moving).
//   SLOW: status snapshot at a fixed 2 Hz. Its fields are filled in by logic 1.
// Because FAST is sent on change only, a still vehicle sends no FAST messages,
// so vehicle liveness comes from SLOW, never from FAST.

export interface FastMsg {
  ch: 'att';
  vehicle: VehicleId;
  /** Sender clock, ms. */
  t: number;
  r: number;
  p: number;
  y: number;
}

export interface SlowMsg {
  ch: 'status';
  vehicle: VehicleId;
  /** Sender clock, ms. */
  t: number;
}

export type LinkBMsg = FastMsg | SlowMsg;

export const attitude: Record<VehicleId, AttitudeTrack> = Object.fromEntries(
  VEHICLES.map((id) => [id, new AttitudeTrack()]),
) as Record<VehicleId, AttitudeTrack>;

export function handleLinkB(msg: LinkBMsg): void {
  if (!(msg.vehicle in vehicles)) return;
  if (msg.ch === 'att') {
    attitude[msg.vehicle].push({ t: msg.t, roll: msg.r, pitch: msg.p, yaw: msg.y });
  } else {
    vehicles[msg.vehicle].markUpdate();
    vehicleBackend.markUpdate();
  }
}
