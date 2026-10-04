import { writable, type Readable } from 'svelte/store';
import { ocs } from './sources';

// Link C: what the OCS (../main.py, link_c.py) reports. Read-only except one operator action, the
// Task 4 ReadinessReport (linkC.ts reportReady). `state` is the whole picture,
// sent every second and on change; `log` lines are the OCS console's [COMMAND] / [ERROR] /
// [VEHICLE] lines, in order. Shapes match link_c_snapshot() in main.py.

export type PreflightStatus = 'PASS' | 'FAIL' | 'WAIT' | 'SKIP' | 'MANUAL' | 'CONFIRM';

/** The OCS's automatic IncidentAck for a Task 4 command (../task4.py). */
export interface Task4Ack {
  vehicle: string | null;
  report_seq: number | null;
  ok: boolean;
  detail: string;
  at: number;
}

export interface KeepOutZone {
  vehicle_type: string; // 'USV' | 'UAV' | ...
  center: [number, number];
  radius_m: number;
  seq: number;
  /** OCS receive time, Unix ms. */
  at: number;
  ack?: Task4Ack | null;
}

/** AssistanceRequest -> IncidentAck -> ReadinessReport (operator) -> ReadinessConfirm. */
export interface AssistanceRequest {
  position: [number, number];
  vehicle_type: string;
  seq: number;
  at: number;
  ack: Task4Ack | null;
  readiness: { report_seq: number | null; ok: boolean; at: number } | null;
  confirmed: { seq: number; at: number } | null;
}

export interface MovingObject {
  position: [number, number];
  heading_deg: number;
  speed_mps: number;
  affected: string[];
  seq: number;
  /** OCS receive time, Unix ms: the position is where it was then. */
  at: number;
}

export interface OcsState {
  ch: 'state';
  /** OCS clock, Unix ms. */
  t: number;
  team_id: string;
  local_test: boolean;
  /** MQTT connection to RoboCommand: 'Connected', 'Reconnecting', 'Disconnected', ... */
  connection: string;
  broker: string | null;
  run: { declared: boolean; declaration_seq: number | null; started: boolean; run_id: number | null };
  tiers: Record<string, string>;
  preflight: {
    items: { key: string; label: string; status: PreflightStatus; detail: string }[];
    passed: number;
    total: number;
    overall: 'READY' | 'CONFIRM' | 'INCOMPLETE' | 'FAIL';
  };
  command: { last: string; counts: { accepted: number; rejected: number; ignored: number } };
  last_error: string;
  mqtt_dropped: number;
  mqtt_stale: number;
  log_dropped: number;
  course: { course_id: string; pinger_freq_hz: number; corners: [number, number][] } | null;
  geofence: [number, number][] | null;
  task4: {
    keep_out_zones: KeepOutZone[];
    moving_object: MovingObject | null;
    assistance_request: AssistanceRequest | null;
    readiness_confirm: { report_seq: number; vehicle_id: string; seq: number; at: number } | null;
    last_all_clear?: { vehicle_type: string; seq: number; at: number; ack: Task4Ack | null } | null;
  };
  /** Link A per vehicle as the OCS sees it ('LIVE', 'LOST', 'NO BACKEND', ...); null in local test mode. */
  vehicles: Record<string, string> | null;
}

export interface OcsLogLine {
  ch: 'log';
  t: number;
  kind: 'command' | 'error' | 'vehicle';
  text: string;
}

export type LinkCMsg = OcsState | OcsLogLine;

/** Lines kept for the panel (the OCS replays its last 100 to a new display). */
export const OCS_LOG_MAX = 200;

const stateStore = writable<OcsState | undefined>(undefined);
const logStore = writable<readonly OcsLogLine[]>([]);

/** The latest OCS state. Show it as live only while the `ocs` Source is 'live'. */
export const ocsState: Readable<OcsState | undefined> = stateStore;
export const ocsLog: Readable<readonly OcsLogLine[]> = logStore;

export function handleLinkC(msg: LinkCMsg): void {
  ocs.markUpdate();
  if (msg.ch === 'state') {
    stateStore.set(msg);
  } else if (msg.ch === 'log') {
    logStore.update((l) => {
      const next = [...l, msg];
      return next.length > OCS_LOG_MAX ? next.slice(next.length - OCS_LOG_MAX) : next;
    });
  }
}

/** A reconnecting feed replays recent lines: start the log afresh so nothing shows twice. */
export function clearOcsLog(): void {
  logStore.set([]);
}
