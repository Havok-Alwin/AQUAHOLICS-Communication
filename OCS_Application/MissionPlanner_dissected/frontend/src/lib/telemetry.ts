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

/** Vehicle link state in the backend (logic 8, VehicleLink.cs). */
export type VehicleLinkState = 'closed' | 'connecting' | 'live' | 'lost';

export interface VehicleLinkInfo {
  state: VehicleLinkState;
  /** Why the last connect failed or the link dropped; null while connected. */
  error: string | null;
}

/** Backend status, 1 Hz and on every link change. Keeps the backend LIVE when no vehicle is. */
export interface BackendMsg {
  ch: 'backend';
  /** Sender clock, ms. */
  t: number;
  links: Partial<Record<string, VehicleLinkInfo>>;
}

export type LinkBMsg = FastMsg | SlowMsg | ParamsMsg | StatusTextMsg | BackendMsg;

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
  /** Backend receive time, Unix ms (StatusTextMsg.t). */
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

const linkStores = Object.fromEntries(VEHICLES.map((id) => [id, writable<VehicleLinkInfo | undefined>(undefined)])) as Record<
  VehicleId,
  Writable<VehicleLinkInfo | undefined>
>;

/**
 * Each vehicle's link as the backend reports it; undefined until a backend message names it
 * (or while the backend is not connected).
 */
export const vehicleLink: Record<VehicleId, Readable<VehicleLinkInfo | undefined>> = linkStores;

/** The backend went away: no vehicle link state is known any more. */
export function clearVehicleLinks(): void {
  for (const id of VEHICLES) {
    linkStores[id].set(undefined);
    vehicles[id].setConnected(false);
  }
}

export function handleLinkB(msg: LinkBMsg): void {
  // Any message proves the backend is up; the 1 Hz backend message covers the no-vehicle case.
  vehicleBackend.markUpdate();
  if (msg.ch === 'backend') {
    for (const id of VEHICLES) {
      const info = msg.links[id];
      linkStores[id].set(info);
      // Logic 8: anything but LIVE is OFFLINE, with the age of the last update. LOST included:
      // the backend declares it after 1 s without vehicle data, sooner than STALE (2 s after the
      // last SLOW) would, and a page loaded while LOST never says CONNECTING for a silent vehicle.
      vehicles[id].setConnected(info?.state === 'live');
    }
    return;
  }
  if (!(msg.vehicle in vehicles)) return;
  if (msg.ch === 'att') {
    attitude[msg.vehicle].push({ t: msg.t, roll: msg.r, pitch: msg.p, yaw: msg.y });
  } else if (msg.ch === 'params') {
    paramStores[msg.vehicle].set(msg.params);
  } else if (msg.ch === 'statustext') {
    const log = logs[msg.vehicle];
    // Sender time, not arrival: a newly connected page gets the backend's history replayed.
    log.push({ at: msg.t, severity: msg.severity, text: msg.text });
    if (log.length > MESSAGE_LOG_MAX) log.splice(0, log.length - MESSAGE_LOG_MAX);
    logGates[msg.vehicle].push(log.slice());
  } else {
    // Replace, never merge: a field the backend stopped sending must not linger as current.
    gates[msg.vehicle].push(msg.cs);
    vehicles[msg.vehicle].markUpdate();
  }
}
