import { describe, expect, it } from 'vitest';
import { distanceM, movingObjectView, offset, proximityAlerts, Track, vehiclePosition, type LatLng } from './mapModel';
import type { KeepOutZone, MovingObject } from './ocs';

const HOME: LatLng = [1.2966, 103.7764];

describe('distance and offset', () => {
  it('measures metres on the course', () => {
    // 0.001 deg of latitude is ~111.2 m everywhere.
    expect(distanceM(HOME, [HOME[0] + 0.001, HOME[1]])).toBeCloseTo(111.2, 0);
    expect(distanceM(HOME, HOME)).toBe(0);
  });

  it('offset and distance agree', () => {
    for (const heading of [0, 45, 90, 180, 270]) {
      const p = offset(HOME, heading, 25);
      expect(distanceM(HOME, p)).toBeCloseTo(25, 3);
    }
    expect(offset(HOME, 90, 100)[0]).toBeCloseTo(HOME[0], 6); // east keeps the latitude (near the equator)
    expect(offset(HOME, 90, 100)[1]).toBeGreaterThan(HOME[1]);
  });
});

const mo = (at: number, speed = 2, heading = 90): MovingObject => ({
  position: HOME, heading_deg: heading, speed_mps: speed, affected: ['USV'], seq: 1, at,
});

describe('moving object', () => {
  it('is dead-reckoned while fresh', () => {
    const v = movingObjectView(mo(1000), 6000); // 5 s at 2 m/s east
    expect(v.stale).toBe(false);
    expect(distanceM(HOME, v.estimate)).toBeCloseTo(10, 3);
    expect(v.estimate[1]).toBeGreaterThan(HOME[1]);
  });

  it('is STALE at its reported position after 10 s, never hidden', () => {
    const v = movingObjectView(mo(1000), 1000 + 10_001);
    expect(v.stale).toBe(true);
    expect(v.estimate).toEqual(HOME);
  });
});

describe('proximity alerts', () => {
  const zone: KeepOutZone = { vehicle_type: 'USV', center: HOME, radius_m: 15, seq: 2, at: 0 };

  it('flags a vehicle inside a keep-out zone of its type only', () => {
    const inside = offset(HOME, 0, 10);
    expect(proximityAlerts('USV1', 'USV', inside, [zone], null, [])).toHaveLength(1);
    expect(proximityAlerts('UAV1', 'UAV', inside, [zone], null, [])).toHaveLength(0);
    expect(proximityAlerts('USV1', 'USV', offset(HOME, 0, 16), [zone], null, [])).toHaveLength(0);
  });

  it('flags a vehicle 10 m or closer to the estimated moving object', () => {
    const view = movingObjectView(mo(0, 0), 1000);
    expect(proximityAlerts('USV1', 'USV', offset(HOME, 180, 9.5), [], view, ['USV'])).toHaveLength(1);
    expect(proximityAlerts('USV1', 'USV', offset(HOME, 180, 10.5), [], view, ['USV'])).toHaveLength(0);
    expect(proximityAlerts('UAV1', 'UAV', offset(HOME, 180, 5), [], view, ['USV'])).toHaveLength(0); // not affected
  });
});

describe('track and position', () => {
  it('caps the track and skips repeated points', () => {
    const t = new Track(3);
    t.add([1, 1]);
    t.add([1, 1]);
    t.add([1, 2]);
    t.add([1, 3]);
    t.add([1, 4]);
    expect(t.latLngs).toEqual([[1, 2], [1, 3], [1, 4]]);
  });

  it('uses a position only with a 3D fix, never 0,0', () => {
    expect(vehiclePosition({ lat: 1, lng: 2, gpsstatus: 3 })).toEqual([1, 2]);
    expect(vehiclePosition({ lat: 1, lng: 2, gpsstatus: 2 })).toBeNull();
    expect(vehiclePosition({ lat: 0, lng: 0, gpsstatus: 3 })).toBeNull();
    expect(vehiclePosition({})).toBeNull();
  });
});
