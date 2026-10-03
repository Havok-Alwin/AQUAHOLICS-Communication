import { writable, type Readable, type Writable } from 'svelte/store';
import { AttitudeTrack } from './attitude';
import { VEHICLES, type VehicleId } from './config';
import type { CurrentStateFields } from './currentState';
import { createGate, type Gate } from './gate';
import { MESSAGE_LOG_MAX } from './severity';
import { vehicleBackend, vehicles } from './sources';
import type { BatteryParams } from './warnings';

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

/** Vehicle parameters the frontend needs (logic 6). Sent on connect and whenever they change. */
export interface ParamsMsg {
  ch: 'params';
  vehicle: VehicleId;
  params: BatteryParams;
}

/** Logic 7: every STATUSTEXT from the vehicle, any severity, forwarded as it arrives (event-driven). */
export interface StatusTextMsg {
  ch: 'statustext';
  vehicle: VehicleId;
  /** Sender clock, ms. */
  t: number;
  severity: number; // MAV_SEVERITY
  text: string;
}

export type LinkBMsg = FastMsg | SlowMsg | ParamsMsg | StatusTextMsg;

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

const paramStores = Object.fromEntries(VEHICLES.map((id) => [id, writable<BatteryParams | undefined>(undefined)])) as Record<
  VehicleId,
  Writable<BatteryParams | undefined>
>;

/** Latest vehicle parameters per vehicle; undefined until the backend sends them. */
export const vehicleParams: Record<VehicleId, Readable<BatteryParams | undefined>> = paramStores;

export interface LogEntry {
  /** Local receive time (Date.now()). */
  at: number;
  severity: number;
  text: string;
}

// Logic 7: message log per vehicle, newest last, capped like MP's cs.messages.
// Entries are appended immediately (never dropped); the UI sees the list through the gate.
const logs = Object.fromEntries(VEHICLES.map((id) => [id, [] as LogEntry[]])) as Record<VehicleId, LogEntry[]>;
const logStores = Object.fromEntries(VEHICLES.map((id) => [id, writable<readonly LogEntry[]>([])])) as Record<
  VehicleId,
  Writable<readonly LogEntry[]>
>;
const logGates = Object.fromEntries(
  VEHICLES.map((id) => [id, createGate<readonly LogEntry[]>((list) => logStores[id].set(list))]),
) as Record<VehicleId, Gate<readonly LogEntry[]>>;

export const vehicleLog: Record<VehicleId, Readable<readonly LogEntry[]>> = logStores;

export function handleLinkB(msg: LinkBMsg): void {
  if (!(msg.vehicle in vehicles)) return;
  if (msg.ch === 'att') {
    attitude[msg.vehicle].push({ t: msg.t, roll: msg.r, pitch: msg.p, yaw: msg.y });
  } else if (msg.ch === 'params') {
    paramStores[msg.vehicle].set(msg.params);
  } else if (msg.ch === 'statustext') {
    const log = logs[msg.vehicle];
    log.push({ at: Date.now(), severity: msg.severity, text: msg.text });
    if (log.length > MESSAGE_LOG_MAX) log.splice(0, log.length - MESSAGE_LOG_MAX);
    logGates[msg.vehicle].push(log.slice());
  } else {
    // Replace, never merge: a field the backend stopped sending must not linger as current.
    gates[msg.vehicle].push(msg.cs);
    vehicles[msg.vehicle].markUpdate();
    vehicleBackend.markUpdate();
  }
}
