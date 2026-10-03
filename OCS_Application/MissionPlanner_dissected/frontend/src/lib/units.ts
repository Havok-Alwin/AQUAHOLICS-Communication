import { writable } from 'svelte/store';

// Logic 10: display units, ported from Mission Planner 1.3.x MainV2.ChangeUnits (decompiled).
//
// MP converts inside the CurrentState getters with process-wide static multipliers. We do NOT do
// that in the backend: the same CurrentState feeds the link A heartbeat (spd_mps, metres), so the
// backend leaves the multipliers at 1 and everything on link B is SI. The frontend converts for
// display only, with MP's factors and labels.
//
// Which CurrentState fields MP converts:
//   speed: groundspeed, airspeed, verticalspeed, targetairspeed (and wind_vel, climbrate)
//   alt:   alt, altasl, targetalt
//   dist:  wp_dist, DistToHome
// Not converted by MP: HomeAlt (raw metres even when alt is in feet, so MP's HUD ground band is
// wrong in feet) and xtrack_error. We convert HomeAlt with the alt unit so it matches alt, and
// leave xtrack_error in metres like MP.

export type UnitKind = 'speed' | 'alt' | 'dist';

export const SPEED_UNITS = {
  'm/s': 1,
  fps: 3.28084,
  kph: 3.6,
  mph: 2.2369363,
  kts: 1.9438444,
} as const;
export const LENGTH_UNITS = { m: 1, ft: 3.28084 } as const;

export type SpeedUnit = keyof typeof SPEED_UNITS;
export type LengthUnit = keyof typeof LENGTH_UNITS;

export interface DisplayUnits {
  speed: SpeedUnit;
  alt: LengthUnit;
  dist: LengthUnit;
}

/** MP defaults when no setting exists. */
export const DEFAULT_UNITS: DisplayUnits = { speed: 'm/s', alt: 'm', dist: 'm' };

export function factor(units: DisplayUnits, kind: UnitKind): number {
  return kind === 'speed' ? SPEED_UNITS[units.speed] : LENGTH_UNITS[units[kind]];
}

export function label(units: DisplayUnits, kind: UnitKind): string {
  return units[kind];
}

/** SI value -> display value. */
export function toDisplay(units: DisplayUnits, kind: UnitKind, si: number): number {
  return si * factor(units, kind);
}

// Per-viewer convenience: remembered in this browser only. Wire data is always SI.
const KEY = 'ocs.displayUnits';

function load(): DisplayUnits {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULT_UNITS;
    const u = JSON.parse(raw) as Partial<DisplayUnits>;
    return {
      speed: u.speed && u.speed in SPEED_UNITS ? u.speed : DEFAULT_UNITS.speed,
      alt: u.alt && u.alt in LENGTH_UNITS ? u.alt : DEFAULT_UNITS.alt,
      dist: u.dist && u.dist in LENGTH_UNITS ? u.dist : DEFAULT_UNITS.dist,
    };
  } catch {
    return DEFAULT_UNITS;
  }
}

export const displayUnits = writable<DisplayUnits>(load());

displayUnits.subscribe((u) => {
  try {
    localStorage.setItem(KEY, JSON.stringify(u));
  } catch {
    // storage unavailable: units still work for this session
  }
});
