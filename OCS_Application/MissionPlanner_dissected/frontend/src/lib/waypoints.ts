// Planner model, kept free of Leaflet and the DOM so it can be tested. Coordinates are [lat, lng].
// File format: Mission Planner / QGC "QGC WPL 110" text, so missions move between this planner and MP.
import { distanceM, type LatLng } from './mapModel';
import { MAVCMD, type CmdDef, type CmdSet } from './mavcmd';
import type { KeepOutZone } from './ocs';

/** MAV_CMD ids the planner code itself refers to. Every other command comes from `MAVCMD` (logic 13). */
export const CMD = { WAYPOINT: 16, LOITER_UNLIM: 17, LOITER_TIME: 19, RETURN_TO_LAUNCH: 20, DO_SET_ROI: 201 } as const;

export interface Waypoint {
  cmd: number;
  lat: number;
  lng: number;
  alt: number;
  /** MAVLink param1..4 (meaning per command: see `MAVCMD`). */
  p: [number, number, number, number];
  /** MAV_FRAME: 3 = global, relative alt. */
  frame: number;
}

/**
 * MP's FlightPlanner.readCMDXML picks the list by cs.firmware: ArduPlane -> APM, ArduRover -> APRover,
 * anything else -> AC2 (copter). Link B does not carry the firmware, so we pick by vehicle type:
 * the USV runs ArduRover, the UAV ArduCopter.
 */
export const CMD_SET: Record<'USV' | 'UAV', CmdSet> = { USV: 'APRover', UAV: 'AC2' };

const ALL = new Map<number, CmdDef>();
for (const list of Object.values(MAVCMD)) for (const d of list) if (!ALL.has(d[1])) ALL.set(d[1], d);

/** The command's definition (name + column names) from any firmware list; undefined for an unknown id. */
export const cmdDef = (cmd: number): CmdDef | undefined => ALL.get(cmd);
export const cmdName = (cmd: number): string => ALL.get(cmd)?.[0] ?? `CMD ${cmd}`;
/** Column names P1..P4, X, Y, Z; MP shows "setme" for a command it does not know. */
export const columnNames = (cmd: number): readonly string[] => ALL.get(cmd)?.[2] ?? Array(7).fill('setme');
/** Does this command take a location (its X column is named, e.g. "Lat")? */
export const takesPosition = (cmd: number): boolean => (ALL.get(cmd)?.[2][4] ?? '') !== '';

export function newWaypoint(at: LatLng, alt: number, cmd: number = CMD.WAYPOINT): Waypoint {
  return { cmd, lat: at[0], lng: at[1], alt, p: [0, 0, 0, 0], frame: 3 };
}

/**
 * Change a row's command. Columns the new command does not use are zeroed, so the row reads (and is
 * written to the vehicle/file) the way MP shows it: blank header, 0 value.
 */
export function setCommand(w: Waypoint, cmd: number): void {
  const names = columnNames(cmd);
  w.cmd = cmd;
  for (let i = 0; i < 4; i++) if (names[i] === '') w.p[i] = 0;
  if (names[4] === '') w.lat = 0;
  if (names[5] === '') w.lng = 0;
  if (names[6] === '') w.alt = 0;
}

/**
 * Drawn as a numbered marker on the route. MP WPOverlay: any command with a non-zero lat/lng, except
 * DO_SET_ROI, which gets its own red marker off the route.
 */
export const hasPosition = (w: Waypoint): boolean => w.cmd !== CMD.DO_SET_ROI && !(w.lat === 0 && w.lng === 0);

export interface Home {
  lat: number;
  lng: number;
  alt: number;
}

/** Waypoints that carry a position, in order, starting at home when it is set. */
export function route(home: Home | null, wps: Waypoint[]): LatLng[] {
  const pts: LatLng[] = home ? [[home.lat, home.lng]] : [];
  for (const w of wps) if (hasPosition(w)) pts.push([w.lat, w.lng]);
  if (home && wps.some((w) => w.cmd === CMD.RETURN_TO_LAUNCH)) pts.push([home.lat, home.lng]);
  return pts;
}

export function totalDistanceM(home: Home | null, wps: Waypoint[]): number {
  const pts = route(home, wps);
  let d = 0;
  for (let i = 1; i < pts.length; i++) d += distanceM(pts[i - 1]!, pts[i]!);
  return d;
}

/** Ray casting; the polygon is the course/geofence corner list. */
export function insidePolygon(p: LatLng, poly: LatLng[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [yi, xi] = poly[i]!;
    const [yj, xj] = poly[j]!;
    if (yi > p[0] !== yj > p[0] && p[1] < ((xj - xi) * (p[0] - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export interface Issue {
  /** Index into the waypoint list. */
  index: number;
  text: string;
}

/** Waypoints outside the boundary (course for a USV, geofence for a UAV) or inside a keep-out zone for this vehicle type. */
export function checkWaypoints(
  wps: Waypoint[],
  boundary: LatLng[],
  vehicleType: 'USV' | 'UAV',
  zones: KeepOutZone[],
): Issue[] {
  const issues: Issue[] = [];
  wps.forEach((w, index) => {
    if (!hasPosition(w)) return;
    const at: LatLng = [w.lat, w.lng];
    if (boundary.length > 2 && !insidePolygon(at, boundary)) issues.push({ index, text: `WP ${index + 1} is outside the ${vehicleType === 'UAV' ? 'geofence' : 'course'}` });
    for (const z of zones)
      if (z.vehicle_type === vehicleType && distanceM(at, z.center as LatLng) < z.radius_m)
        issues.push({ index, text: `WP ${index + 1} is inside a keep-out zone (${z.radius_m} m)` });
  });
  return issues;
}

const fix = (n: number, dp: number) => n.toFixed(dp);

/** Home is line 0 (frame 0), as MP writes it. */
export function toWpl(home: Home | null, wps: Waypoint[]): string {
  const rows = [
    ['0', '1', '0', '16', '0', '0', '0', '0', fix(home?.lat ?? 0, 8), fix(home?.lng ?? 0, 8), fix(home?.alt ?? 0, 6), '1'],
    ...wps.map((w, i) => [
      String(i + 1), '0', String(w.frame), String(w.cmd),
      ...w.p.map((x) => fix(x, 8)),
      fix(w.lat, 8), fix(w.lng, 8), fix(w.alt, 6), '1',
    ]),
  ];
  return 'QGC WPL 110\n' + rows.map((r) => r.join('\t')).join('\n') + '\n';
}

/** Throws with a readable message on a file that is not a WPL 110 mission. */
export function fromWpl(text: string): { home: Home | null; wps: Waypoint[] } {
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== '');
  if (!/^QGC WPL/.test(lines[0] ?? '')) throw new Error('Not a Mission Planner waypoint file (missing "QGC WPL" header)');
  let home: Home | null = null;
  const wps: Waypoint[] = [];
  for (const [n, line] of lines.slice(1).entries()) {
    const f = line.trim().split(/\s+/);
    if (f.length < 11) throw new Error(`Line ${n + 2}: expected 12 columns, got ${f.length}`);
    const v = f.map(Number) as [number, number, number, number, number, number, number, number, number, number, number];
    if (v.slice(0, 11).some((x) => !Number.isFinite(x))) throw new Error(`Line ${n + 2}: a column is not a number`);
    if (n === 0 && v[2] === 0) {
      home = v[8] === 0 && v[9] === 0 ? null : { lat: v[8], lng: v[9], alt: v[10] };
      continue;
    }
    wps.push({ cmd: v[3], frame: v[2], p: [v[4], v[5], v[6], v[7]], lat: v[8], lng: v[9], alt: v[10] });
  }
  return { home, wps };
}

// ---- Logic 12: map location (MP FlightPlanner / FlightData, see CLAUDE.md) ----

/** Initial bearing a -> b in degrees 0..360 (MP lbl_prevdist "AZ"). */
export function bearingDeg(a: LatLng, b: LatLng): number {
  const r = Math.PI / 180;
  const y = Math.sin((b[1] - a[1]) * r) * Math.cos(b[0] * r);
  const x = Math.cos(a[0] * r) * Math.sin(b[0] * r) - Math.sin(a[0] * r) * Math.cos(b[0] * r) * Math.cos((b[1] - a[1]) * r);
  return ((Math.atan2(y, x) / r) + 360) % 360;
}

/**
 * MP SetMouseDisplay: distance and azimuth from the last planned point to the cursor (lbl_prevdist),
 * and distance from home (lbl_homedist). MP's pointlist starts with home, so with no waypoints
 * "previous" is home.
 */
export function cursorInfo(cursor: LatLng, home: Home | null, wps: Waypoint[]): { prev: { dist: number; az: number } | null; home: number | null } {
  const pts = route(home, wps.filter((w) => w.cmd !== CMD.RETURN_TO_LAUNCH));
  const last = pts[pts.length - 1];
  return {
    prev: last ? { dist: distanceM(last, cursor), az: bearingDeg(last, cursor) } : null,
    home: home ? distanceM([home.lat, home.lng], cursor) : null,
  };
}

/**
 * "Go to location" input. MP (zoomToToolStripMenuItem) only geocodes a place name; we first accept
 * coordinates, "lat, lng" or "lat lng" in decimal degrees, so it works without Internet.
 * Returns null when the text is not a coordinate pair (then it is a place name).
 */
export function parseLatLng(text: string): LatLng | null {
  const m = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const lat = Number(m[1]);
  const lng = Number(m[2]);
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  return [lat, lng];
}

/** MP zoom rules: vehicle/home zoom in to at least 17, a found place to exactly 15, a planned home on load to 16. */
export const ZOOM = { vehicleOrHomeMin: 17, place: 15, homeOnLoad: 16, unknownWorld: 3 } as const;

/**
 * Where the planner opens when there is no home, no saved view and no course: the team's test site,
 * RMK Engineering College, Kavaraipettai (OpenStreetMap, 2026-10-05). Also a Go-to shortcut ("RMK").
 */
export const DEFAULT_PLACE = { name: 'RMK', lat: 13.3568164, lng: 80.1422648, zoom: 17 } as const;

/** Named Go-to shortcuts (case-insensitive), checked before coordinates and the online geocoder. */
export function findPlace(text: string, places: Record<string, LatLng>): LatLng | null {
  const key = text.trim().toLowerCase();
  for (const [name, at] of Object.entries(places)) if (name.toLowerCase() === key) return at;
  return null;
}

export interface MapView {
  lat: number;
  lng: number;
  zoom: number;
}

/**
 * Where the planner map opens. MP: FlightPlanner_Load centres on the planned home at zoom 16;
 * otherwise the map keeps FlightData's last position (Settings maplast_lat/lng/zoom), and a saved
 * latitude that rounds to 0.0 means "never set": zoom 3. We add a last fallback: the course.
 */
export function initialView(home: Home | null, last: MapView | null): MapView | 'course' {
  if (home) return { lat: home.lat, lng: home.lng, zoom: ZOOM.homeOnLoad };
  if (last && Number.isFinite(last.lat) && Number.isFinite(last.lng)) {
    if (Math.round(last.lat * 10) / 10 === 0) return { lat: last.lat, lng: last.lng, zoom: ZOOM.unknownWorld };
    return last;
  }
  return 'course';
}
