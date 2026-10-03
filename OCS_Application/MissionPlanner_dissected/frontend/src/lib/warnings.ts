import type { CurrentStateFields } from './currentState';

// Logic 6: warning thresholds, ported exactly from Mission Planner 1.3.x (decompiled):
//   battery    FlightData.mainloop  -> hud1.lowvoltagealert / criticalvoltagealert
//   GPS, EKF, vibration, link, CPU, pre-arm, SAFE, FAILSAFE   HUD.doPaint colour rules
// Not ported: hud1.lowairspeed / lowgroundspeed. MP only ever sets them false (the low-speed
// check exists only as an optional speech alert in MainV2), so the HUD never shows them.
// failsafe and safetyactive are computed inside CurrentState (backend), bound as-is.

export type Level = 'critical' | 'warn';

export interface Warning {
  id: string;
  text: string;
  level: Level;
}

/** Vehicle parameters MP reads for battery alerts (MAV.param). Sent by the backend on connect. */
export interface BatteryParams {
  BATT_LOW_VOLT?: number;
  BATT_CRT_VOLT?: number;
  BATT_LOW_MAH?: number;
  BATT_CRT_MAH?: number;
  BATT_CAPACITY?: number;
}

export interface BatteryThresholds {
  lowVolt: number;
  lowPct: number;
  critVolt: number;
  critPct: number;
}

/**
 * FlightData.mainloop:
 *   low volt  = BATT_LOW_VOLT                      (else MP setting speechbatteryvolt)
 *   low %     = BATT_LOW_MAH / BATT_CAPACITY * 100 if BATT_LOW_MAH > 0 (else speechbatterypercent)
 *   crit volt = BATT_CRT_VOLT                      (else low volt)
 *   crit %    = BATT_CRT_MAH / BATT_CAPACITY * 100 if BATT_CRT_MAH > 0 (else low %)
 * Returns null when there is no voltage threshold at all (params not received and no fallback):
 * MP would then compare against 0; we show "thresholds unknown" instead of a false OK.
 */
export function batteryThresholds(p: BatteryParams | undefined, fallback?: Partial<BatteryThresholds>): BatteryThresholds | null {
  let lowVolt = p?.BATT_LOW_VOLT ?? 0;
  let lowPct =
    p?.BATT_LOW_MAH !== undefined && p.BATT_CAPACITY !== undefined && p.BATT_LOW_MAH > 0
      ? (p.BATT_LOW_MAH / p.BATT_CAPACITY) * 100
      : 0;
  let critVolt = p?.BATT_CRT_VOLT ?? 0;
  let critPct =
    p?.BATT_CRT_MAH !== undefined && p.BATT_CAPACITY !== undefined && p.BATT_CRT_MAH > 0
      ? (p.BATT_CRT_MAH / p.BATT_CAPACITY) * 100
      : 0;
  if (lowVolt === 0) lowVolt = fallback?.lowVolt ?? 0;
  if (lowPct === 0) lowPct = fallback?.lowPct ?? 0;
  if (critVolt === 0) critVolt = lowVolt;
  if (critPct === 0) critPct = lowPct;
  if (lowVolt === 0 && lowPct === 0) return null;
  return { lowVolt, lowPct, critVolt, critPct };
}

export type BatteryLevel = 'critical' | 'low' | 'ok' | 'unknown';

/** voltage <= volt threshold, else remaining < % threshold (MP order and comparisons). */
export function batteryLevel(cs: Partial<CurrentStateFields>, t: BatteryThresholds | null): BatteryLevel {
  const v = cs.battery_voltage;
  const pct = cs.battery_remaining;
  if (t === null || v === undefined || pct === undefined) return 'unknown';
  if (v <= t.critVolt || pct < t.critPct) return 'critical';
  if (v <= t.lowVolt || pct < t.lowPct) return 'low';
  return 'ok';
}

/** Everything the operator must see, worst first. Empty = no warnings. */
export function warnings(cs: Partial<CurrentStateFields>, battery: BatteryLevel): Warning[] {
  const out: Warning[] = [];
  const add = (id: string, text: string, level: Level) => out.push({ id, text, level });

  if (cs.failsafe) add('failsafe', 'FAILSAFE', 'critical');
  if (battery === 'critical') add('batt', 'BATTERY CRITICAL', 'critical');
  else if (battery === 'low') add('batt', 'BATTERY LOW', 'warn');
  // HUD: GPS fix 0 (No GPS) and 1 (No Fix) are red; 2D and better are not.
  if (cs.gpsstatus === 0) add('gps', 'NO GPS', 'critical');
  else if (cs.gpsstatus === 1) add('gps', 'GPS NO FIX', 'critical');
  // HUD: linkqualitygcs == 0 draws a red cross (CurrentState sets 0 after 10 s without a valid packet).
  if (cs.linkqualitygcs === 0) add('link', 'LINK 0%', 'critical');
  // HUD: ekfstatus > 0.8 red, > 0.5 orange.
  if (cs.ekfstatus !== undefined) {
    if (cs.ekfstatus > 0.8) add('ekf', 'EKF', 'critical');
    else if (cs.ekfstatus > 0.5) add('ekf', 'EKF', 'warn');
  }
  // HUD: any vibe axis > 60 red, > 30 orange.
  const vibe = Math.max(cs.vibex ?? 0, cs.vibey ?? 0, cs.vibez ?? 0);
  if (vibe > 60) add('vibe', 'VIBRATION', 'critical');
  else if (vibe > 30) add('vibe', 'VIBRATION', 'warn');
  // HUD: load == 100 -> red "CPU".
  if (cs.load === 100) add('cpu', 'CPU 100%', 'critical');
  // HUD: SAFE in red while the safety switch is active.
  if (cs.safetyactive) add('safe', 'SAFETY ON', 'critical');
  // HUD: pre-arm is only shown while disarmed; red when not ready.
  if (cs.armed === false && cs.prearmstatus === false) add('prearm', 'NOT READY TO ARM', 'critical');

  return out.sort((a, b) => (a.level === b.level ? 0 : a.level === 'critical' ? -1 : 1));
}
