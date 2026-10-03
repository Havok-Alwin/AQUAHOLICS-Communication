import { writable, type Readable, type Writable } from 'svelte/store';
import { AttitudeTrack } from './attitude';
import { VEHICLES, type VehicleId } from './config';
import type { CurrentStateFields } from './currentState';
import { createGate, type Gate } from './gate';
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
  /** Full snapshot of the bound CurrentState fields (logic 1). A missing field shows as '—'. */
  cs: Partial<CurrentStateFields>;
}

export type LinkBMsg = FastMsg | SlowMsg;

export const attitude: Record<VehicleId, AttitudeTrack> = Object.fromEntries(
  VEHICLES.map((id) => [id, new AttitudeTrack()]),
) as Record<VehicleId, AttitudeTrack>;

type Snapshot = Partial<CurrentStateFields>;

const stores = Object.fromEntries(VEHICLES.map((id) => [id, writable<Snapshot>({})])) as Record<
  VehicleId,
  Writable<Snapshot>
>;

/** Latest SLOW snapshot per vehicle. Display it as live only while the vehicle Source is 'live'. */
export const vehicleState: Record<VehicleId, Readable<Snapshot>> = stores;

// Logic 2: snapshots reach the UI through the update gate (<= 10 Hz, latest wins).
// Freshness (markUpdate) is NOT gated: staleness must use the true arrival time.
const gates = Object.fromEntries(
  VEHICLES.map((id) => [id, createGate<Snapshot>((cs) => stores[id].set(cs))]),
) as Record<VehicleId, Gate<Snapshot>>;

export function handleLinkB(msg: LinkBMsg): void {
  if (!(msg.vehicle in vehicles)) return;
  if (msg.ch === 'att') {
    attitude[msg.vehicle].push({ t: msg.t, roll: msg.r, pitch: msg.p, yaw: msg.y });
  } else {
    // Replace, never merge: a field the backend stopped sending must not linger as current.
    gates[msg.vehicle].push(msg.cs);
    vehicles[msg.vehicle].markUpdate();
    vehicleBackend.markUpdate();
  }
}
