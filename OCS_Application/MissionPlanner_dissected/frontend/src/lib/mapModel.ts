// The map's model, kept free of Leaflet so it can be tested: distances, the Task 4 moving object,
// keep-out checks and vehicle tracks. Coordinates are [lat, lng] in degrees.
import type { KeepOutZone, MovingObject } from './ocs';
import { MAP_OBJECT_MAX_AGE_MS, MAP_TRACK_POINTS } from './pacing';

export type LatLng = [number, number];

const EARTH_R = 6371008.8; // mean radius, m
const rad = (d: number) => (d * Math.PI) / 180;

/** Great-circle distance in metres (haversine). Exact enough for a course a few hundred metres wide. */
export function distanceM(a: LatLng, b: LatLng): number {
  const dLat = rad(b[0] - a[0]);
  const dLng = rad(b[1] - a[1]);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[0])) * Math.cos(rad(b[0])) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_R * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** The point `distM` metres from `from` along `headingDeg` (0 = north, 90 = east). */
export function offset(from: LatLng, headingDeg: number, distM: number): LatLng {
  const d = distM / EARTH_R;
  const h = rad(headingDeg);
  const lat1 = rad(from[0]);
  const lat2 = Math.asin(Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(h));
  const lng2 = rad(from[1]) + Math.atan2(Math.sin(h) * Math.sin(d) * Math.cos(lat1), Math.cos(d) - Math.sin(lat1) * Math.sin(lat2));
  return [(lat2 * 180) / Math.PI, (((lng2 * 180) / Math.PI + 540) % 360) - 180];
}

/** Task 4: stay more than this from the moving object (handbook). */
export const MOVING_OBJECT_MIN_DISTANCE_M = 10;

export interface MovingObjectView {
  /** Where RoboCommand reported it. */
  reported: LatLng;
  /** Best estimate now: dead-reckoned from the report while fresh, else the reported position. */
  estimate: LatLng;
  ageMs: number;
  /** Older than MAP_OBJECT_MAX_AGE_MS: drawn STALE at its last reported position, not hidden. */
  stale: boolean;
}

/**
 * Where the moving object is now. A MovingObjectAlert gives position, heading and speed at one
 * moment; between alerts it is dead-reckoned. After MAP_OBJECT_MAX_AGE_MS the estimate would only
 * grow less trustworthy, so it falls back to the reported position and is marked STALE.
 */
export function movingObjectView(mo: MovingObject, nowMs: number): MovingObjectView {
  const ageMs = Math.max(0, nowMs - mo.at);
  const stale = ageMs > MAP_OBJECT_MAX_AGE_MS;
  const estimate = stale || mo.speed_mps <= 0 ? mo.position : offset(mo.position, mo.heading_deg, (mo.speed_mps * ageMs) / 1000);
  return { reported: mo.position, estimate, ageMs, stale };
}

export interface ProximityAlert {
  vehicle: string;
  text: string;
}

/**
 * Task 4 safety checks for one vehicle: inside a keep-out zone for its type, or within
 * MOVING_OBJECT_MIN_DISTANCE_M of the moving object (if it affects that type). A STALE moving object
 * is still checked at its last reported position (CLAUDE.md, logic 11 deviation).
 */
export function proximityAlerts(
  vehicle: string,
  vehicleType: string,
  pos: LatLng,
  zones: readonly KeepOutZone[],
  mo: MovingObjectView | null,
  moAffected: readonly string[],
): ProximityAlert[] {
  const alerts: ProximityAlert[] = [];
  for (const z of zones) {
    if (z.vehicle_type !== vehicleType) continue;
    const d = distanceM(pos, z.center);
    if (d <= z.radius_m) alerts.push({ vehicle, text: `${vehicle} INSIDE keep-out zone (${d.toFixed(0)} m from centre, radius ${z.radius_m} m)` });
  }
  if (mo && (moAffected.length === 0 || moAffected.includes(vehicleType))) {
    const d = distanceM(pos, mo.estimate);
    if (d <= MOVING_OBJECT_MIN_DISTANCE_M)
      alerts.push({ vehicle, text: `${vehicle} ${d.toFixed(1)} m from the moving object (keep > ${MOVING_OBJECT_MIN_DISTANCE_M} m)${mo.stale ? ', object position STALE' : ''}` });
  }
  return alerts;
}

/** A vehicle's recent positions, capped like MP's NUM_tracklength. */
export class Track {
  private readonly points: LatLng[] = [];
  constructor(private readonly max = MAP_TRACK_POINTS) {}

  /** Adds a point if the vehicle moved (a still vehicle does not fill the track). */
  add(p: LatLng): void {
    const last = this.points[this.points.length - 1];
    if (last && last[0] === p[0] && last[1] === p[1]) return;
    this.points.push(p);
    if (this.points.length > this.max) this.points.splice(0, this.points.length - this.max);
  }

  get latLngs(): readonly LatLng[] {
    return this.points;
  }
}

/** A usable vehicle position from the SLOW snapshot: a 3D fix, as for the link A heartbeat. */
export function vehiclePosition(cs: { lat?: number; lng?: number; gpsstatus?: number }): LatLng | null {
  if (cs.lat === undefined || cs.lng === undefined || cs.gpsstatus === undefined) return null;
  if (cs.gpsstatus < 3 || (cs.lat === 0 && cs.lng === 0)) return null;
  return [cs.lat, cs.lng];
}
