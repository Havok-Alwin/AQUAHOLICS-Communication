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
export type VehicleLinkState = 'off' | 'closed' | 'connecting' | 'live' | 'lost';

export interface VehicleLinkInfo {
  /** 'off': no port chosen (connect it from the vehicle card). */
  state: VehicleLinkState;
  /** Why the last connect failed or the link dropped; null while connected. */
  error: string | null;
  /** The port this vehicle is connected (or connecting) to; absent while 'off'. */
  port?: string;
  baud?: number;
  sysid?: number;
}

/** A serial port the backend found (link B `backend.ports`). */
export interface SerialPortInfo {
  path: string;
  label: string;
}

/** Backend status, 1 Hz and on every link change. Keeps the backend LIVE when no vehicle is. */
export interface BackendMsg {
  ch: 'backend';
  /** Sender clock, ms. */
  t: number;
  links: Partial<Record<string, VehicleLinkInfo>>;
  /** Serial ports on the OCS laptop, refreshed every second. */
  ports?: SerialPortInfo[];
}

/** Logic 9: the vehicle's flight modes, MP's names for its firmware (the names cs.mode uses). */
export interface ModesMsg {
  ch: 'modes';
  vehicle: VehicleId;
  modes: string[];
}

export type CommandKind = 'arm' | 'disarm' | 'mode';
/** 'sending' is local (not yet confirmed by the backend); the rest come from the backend. */
export type CommandStatus = 'sending' | 'sent' | 'accepted' | 'rejected' | 'timeout' | 'error';

/** Logic 9: progress of a command (VehicleCommands.cs): sent, then one final status. */
export interface CmdAckMsg {
  ch: 'cmdack';
  vehicle: VehicleId;
  id: string;
  cmd: CommandKind | 'connect' | 'disconnect';
  status: Exclude<CommandStatus, 'sending'>;
  detail: string;
  /** Sender clock, ms. */
  t: number;
}

export type LinkBMsg = FastMsg | SlowMsg | ParamsMsg | StatusTextMsg | BackendMsg | ModesMsg | CmdAckMsg;

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

const modeStores = Object.fromEntries(VEHICLES.map((id) => [id, writable<readonly string[]>([])])) as Record<
  VehicleId,
  Writable<readonly string[]>
>;

/** Flight modes the operator can choose for each vehicle; empty while it is not connected. */
export const vehicleModes: Record<VehicleId, Readable<readonly string[]>> = modeStores;

export interface CommandState {
  id: string;
  cmd: CommandKind;
  /** Mode name for cmd 'mode'. */
  mode?: string;
  status: CommandStatus;
  detail: string;
  /** Local time of the last change, ms. */
  at: number;
}

const commandStores = Object.fromEntries(VEHICLES.map((id) => [id, writable<CommandState | undefined>(undefined)])) as Record<
  VehicleId,
  Writable<CommandState | undefined>
>;

/** The latest command per vehicle and how far it got (logic 9). */
export const vehicleCommand: Record<VehicleId, Readable<CommandState | undefined>> = commandStores;

/** A command just went out on link B (linkB.ts). */
export function commandSending(vehicle: VehicleId, state: CommandState): void {
  commandStores[vehicle].set(state);
}

/** A pending command can no longer be answered (link B closed). */
export function commandsLost(): void {
  for (const id of VEHICLES)
    commandStores[id].update((c) =>
      c && (c.status === 'sending' || c.status === 'sent')
        ? { ...c, status: 'error', detail: 'connection to the vehicle backend lost: outcome unknown', at: Date.now() }
        : c,
    );
}

const portStore = writable<readonly SerialPortInfo[]>([]);

/** Serial ports a vehicle can be connected to. */
export const serialPorts: Readable<readonly SerialPortInfo[]> = portStore;

export interface ConnectReply {
  id: string;
  cmd: 'connect' | 'disconnect';
  status: 'accepted' | 'error';
  detail: string;
  at: number;
}

const connectReplyStores = Object.fromEntries(VEHICLES.map((id) => [id, writable<ConnectReply | undefined>(undefined)])) as Record<
  VehicleId,
  Writable<ConnectReply | undefined>
>;

/** The backend's answer to the last Connect / Disconnect per vehicle (kept apart from vehicle commands). */
export const vehicleConnectReply: Record<VehicleId, Readable<ConnectReply | undefined>> = connectReplyStores;

/** The backend went away: no vehicle link state is known any more. */
export function clearVehicleLinks(): void {
  for (const id of VEHICLES) {
    linkStores[id].set(undefined);
    modeStores[id].set([]);
    vehicles[id].setConnected(false);
  }
}

export function handleLinkB(msg: LinkBMsg): void {
  // Any message proves the backend is up; the 1 Hz backend message covers the no-vehicle case.
  vehicleBackend.markUpdate();
  if (msg.ch === 'backend') {
    if (msg.ports) portStore.set(msg.ports);
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
  if (msg.ch === 'modes') {
    modeStores[msg.vehicle].set(msg.modes);
    return;
  }
  if (msg.ch === 'cmdack' && (msg.cmd === 'connect' || msg.cmd === 'disconnect')) {
    connectReplyStores[msg.vehicle].set({
      id: msg.id,
      cmd: msg.cmd,
      status: msg.status === 'accepted' ? 'accepted' : 'error',
      detail: msg.detail,
      at: Date.now(),
    });
    return;
  }
  if (msg.ch === 'cmdack') {
    // Every display gets every command's progress; a page shows the latest per vehicle.
    commandStores[msg.vehicle].update((c) => ({
      id: msg.id,
      cmd: msg.cmd as CommandKind, // connect / disconnect returned above
      mode: c?.id === msg.id ? c.mode : undefined,
      status: msg.status,
      detail: msg.detail,
      at: Date.now(),
    }));
    return;
  }
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
