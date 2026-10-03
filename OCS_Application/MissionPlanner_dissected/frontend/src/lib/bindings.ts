import type { VehicleType } from './config';
import type { CurrentStateField } from './currentState';

// Logic 1: binding map, read from FlightData.InitializeComponent (MissionPlanner.exe 1.3.x IL).
// MP binds 72 control properties to CurrentState (70 fixed + 2 user-configured QuickViews).
// `mp` is the MP control property, `cs` the CurrentState field it reads.
//
// Ported: hud1 bindings, plus coords1 (lat/lng/alt), lbl_sats, lbl_hdop and QuickView DistToHome.
// Not ported:
//   hud1.roll/pitch/heading      -> FAST channel (logic 0), not SLOW
//   hud1.AOA/SSA/critAOA         fixed-wing aerodynamics; USV and multirotor UAV have none
//   hud1.*2 (battery2, gps2)     second battery / GPS: add if a vehicle has one
//   hud1.datetime                we use our own receive time for staleness
//   hud1.connected               link state is our Source model (logic 8)
//   Gauges, QuickView, payload, wind tabs: duplicates of the above or not needed

export type Group = 'state' | 'motion' | 'position' | 'target' | 'power' | 'gps' | 'health';

export interface Binding {
  /** MP control property (documentation of where this came from). */
  mp: string;
  /** CurrentState field. */
  cs: CurrentStateField;
  label: string;
  group: Group;
  /** CurrentState default unit; made configurable in logic 10. */
  unit?: string;
  /** Decimals for numbers. */
  dp?: number;
  /** Shown only for this vehicle type (e.g. airspeed means nothing on a boat). */
  only?: VehicleType;
}

export const BINDINGS: readonly Binding[] = [
  { mp: 'hud1.status', cs: 'armed', label: 'Armed', group: 'state' },
  { mp: 'hud1.mode', cs: 'mode', label: 'Mode', group: 'state' },
  { mp: 'hud1.failsafe', cs: 'failsafe', label: 'Failsafe', group: 'state' },
  { mp: 'hud1.safetyactive', cs: 'safetyactive', label: 'Safety switch', group: 'state' },
  { mp: 'hud1.prearmstatus', cs: 'prearmstatus', label: 'Pre-arm OK', group: 'state' },
  { mp: 'hud1.ekfstatus', cs: 'ekfstatus', label: 'EKF', group: 'state', dp: 2 },

  { mp: 'hud1.groundspeed', cs: 'groundspeed', label: 'Ground speed', group: 'motion', unit: 'm/s', dp: 1 },
  { mp: 'hud1.airspeed', cs: 'airspeed', only: 'UAV', label: 'Airspeed', group: 'motion', unit: 'm/s', dp: 1 },
  { mp: 'hud1.verticalspeed', cs: 'verticalspeed', only: 'UAV', label: 'Climb', group: 'motion', unit: 'm/s', dp: 1 },
  { mp: 'hud1.groundcourse', cs: 'groundcourse', label: 'Course', group: 'motion', unit: '°', dp: 0 },
  { mp: 'hud1.turnrate', cs: 'turnrate', label: 'Turn rate', group: 'motion', unit: '°/s', dp: 1 },

  { mp: 'coords1.Lat', cs: 'lat', label: 'Lat', group: 'position', dp: 7 },
  { mp: 'coords1.Lng', cs: 'lng', label: 'Lng', group: 'position', dp: 7 },
  { mp: 'hud1.alt', cs: 'alt', label: 'Alt', group: 'position', unit: 'm', dp: 1 },
  { mp: 'hud1.groundalt', cs: 'HomeAlt', label: 'Home alt', group: 'position', unit: 'm', dp: 1 },
  { mp: 'quickView6.number', cs: 'DistToHome', label: 'To home', group: 'position', unit: 'm', dp: 0 },

  { mp: 'hud1.wpno', cs: 'wpno', label: 'WP #', group: 'target', dp: 0 },
  { mp: 'hud1.disttowp', cs: 'wp_dist', label: 'To WP', group: 'target', unit: 'm', dp: 0 },
  { mp: 'hud1.targetheading', cs: 'nav_bearing', label: 'Target hdg', group: 'target', unit: '°', dp: 0 },
  { mp: 'hud1.targetalt', cs: 'targetalt', only: 'UAV', label: 'Target alt', group: 'target', unit: 'm', dp: 1 },
  { mp: 'hud1.targetspeed', cs: 'targetairspeed', label: 'Target speed', group: 'target', unit: 'm/s', dp: 1 },
  { mp: 'hud1.xtrack_error', cs: 'xtrack_error', label: 'X-track err', group: 'target', unit: 'm', dp: 1 },
  { mp: 'hud1.navroll', cs: 'nav_roll', label: 'Nav roll', group: 'target', unit: '°', dp: 1 },
  { mp: 'hud1.navpitch', cs: 'nav_pitch', only: 'UAV', label: 'Nav pitch', group: 'target', unit: '°', dp: 1 },

  { mp: 'hud1.batterylevel', cs: 'battery_voltage', label: 'Battery', group: 'power', unit: 'V', dp: 2 },
  { mp: 'hud1.batteryremaining', cs: 'battery_remaining', label: 'Remaining', group: 'power', unit: '%', dp: 0 },
  { mp: 'hud1.current', cs: 'current', label: 'Current', group: 'power', unit: 'A', dp: 1 },

  { mp: 'hud1.gpsfix', cs: 'gpsstatus', label: 'GPS fix', group: 'gps' },
  { mp: 'lbl_sats.Text', cs: 'satcount', label: 'Sats', group: 'gps', dp: 0 },
  { mp: 'hud1.gpshdop', cs: 'gpshdop', label: 'HDOP', group: 'gps', dp: 2 },

  { mp: 'hud1.linkqualitygcs', cs: 'linkqualitygcs', label: 'Link quality', group: 'health', unit: '%', dp: 0 },
  { mp: 'hud1.load', cs: 'load', label: 'CPU load', group: 'health', unit: '%', dp: 0 },
  { mp: 'hud1.vibex', cs: 'vibex', label: 'Vibe X', group: 'health', dp: 1 },
  { mp: 'hud1.vibey', cs: 'vibey', label: 'Vibe Y', group: 'health', dp: 1 },
  { mp: 'hud1.vibez', cs: 'vibez', label: 'Vibe Z', group: 'health', dp: 1 },
];

// hud1.message <- messageHigh and hud1.messageSeverity <- messageHighSeverity are shown
// as the vehicle's message line, not as table rows (colours by severity are logic 7).

export const GROUP_LABEL: Record<Group, string> = {
  state: 'State',
  motion: 'Motion',
  position: 'Position',
  target: 'Navigation',
  power: 'Power',
  gps: 'GPS',
  health: 'Health',
};

// GPS_FIX_TYPE (MAVLink common.xml)
const GPS_FIX = ['No GPS', 'No fix', '2D', '3D', 'DGPS', 'RTK float', 'RTK fixed', 'Static', 'PPP'];

export function formatBinding(b: Binding, v: unknown): string {
  if (v === undefined || v === null) return '—';
  if (typeof v === 'boolean') {
    if (b.cs === 'armed') return v ? 'ARMED' : 'DISARMED';
    return v ? 'yes' : 'no';
  }
  if (typeof v === 'number') {
    if (!Number.isFinite(v)) return '—';
    if (b.cs === 'gpsstatus') return GPS_FIX[v] ?? String(v);
    const s = v.toFixed(b.dp ?? 1);
    return b.unit ? `${s} ${b.unit}` : s;
  }
  return String(v);
}

export const bindingsFor = (type: VehicleType): Binding[] => BINDINGS.filter((b) => !b.only || b.only === type);
