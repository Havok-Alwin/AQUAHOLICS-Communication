import { describe, expect, it } from 'vitest';
import { CMD, checkWaypoints, fromWpl, insidePolygon, newWaypoint, route, toWpl, totalDistanceM } from './waypoints';
import type { LatLng } from './mapModel';

const SQUARE: LatLng[] = [[0, 0], [0, 0.001], [0.001, 0.001], [0.001, 0]];
const home = { lat: 0.0005, lng: 0.0005, alt: 0 };

describe('geometry', () => {
  it('knows inside from outside', () => {
    expect(insidePolygon([0.0005, 0.0005], SQUARE)).toBe(true);
    expect(insidePolygon([0.002, 0.0005], SQUARE)).toBe(false);
  });
  it('route returns to home after an RTL, and skips its position', () => {
    const wps = [newWaypoint([0.0006, 0.0005], 0), newWaypoint([0, 0], 0, CMD.RETURN_TO_LAUNCH)];
    expect(route(home, wps)).toEqual([[0.0005, 0.0005], [0.0006, 0.0005], [0.0005, 0.0005]]);
    expect(totalDistanceM(home, wps)).toBeCloseTo(2 * 11.1, 0);
  });
});

describe('checks', () => {
  it('flags outside the boundary and inside a keep-out zone of the same vehicle type only', () => {
    const wps = [newWaypoint([0.002, 0.0005], 0), newWaypoint([0.0005, 0.0005], 0)];
    const zones = [
      { center: [0.0005, 0.0005], radius_m: 5, vehicle_type: 'USV' },
      { center: [0.002, 0.0005], radius_m: 5, vehicle_type: 'UAV' },
    ] as never;
    const texts = checkWaypoints(wps, SQUARE, 'USV', zones).map((i) => i.text);
    expect(texts).toEqual(['WP 1 is outside the course', 'WP 2 is inside a keep-out zone (5 m)']);
  });
});

describe('.waypoints files', () => {
  it('round-trips and keeps home as line 0', () => {
    const wps = [newWaypoint([0.0006, 0.0004], 10), { ...newWaypoint([0.0007, 0.0004], 10, CMD.LOITER_TIME), p: [30, 0, 0, 0] as [number, number, number, number] }];
    const text = toWpl(home, wps);
    expect(text.split('\n')[0]).toBe('QGC WPL 110');
    const back = fromWpl(text);
    expect(back.home).toEqual(home);
    expect(back.wps).toEqual(wps);
  });
  it('reads an MP file with home at 0,0 as no home', () => {
    const t = 'QGC WPL 110\n0\t1\t0\t16\t0\t0\t0\t0\t0\t0\t0\t1\n1\t0\t3\t16\t0\t0\t0\t0\t1.2\t103.7\t5\t1\n';
    expect(fromWpl(t).home).toBeNull();
    expect(fromWpl(t).wps).toHaveLength(1);
  });
  it('rejects other files', () => {
    expect(() => fromWpl('hello')).toThrow(/QGC WPL/);
    expect(() => fromWpl('QGC WPL 110\n1\t2')).toThrow(/Line 2/);
  });
});

import { bearingDeg, cursorInfo, initialView, parseLatLng } from './waypoints';

describe('logic 12: map location', () => {
  it('bearing', () => {
    expect(bearingDeg([0, 0], [0.001, 0])).toBeCloseTo(0, 5);
    expect(bearingDeg([0, 0], [0, 0.001])).toBeCloseTo(90, 5);
    expect(bearingDeg([0, 0], [-0.001, 0])).toBeCloseTo(180, 5);
    expect(bearingDeg([0, 0], [0, -0.001])).toBeCloseTo(270, 5);
  });
  it('cursor info: previous is home when there are no waypoints, RTL is skipped', () => {
    expect(cursorInfo([0.001, 0], null, [])).toEqual({ prev: null, home: null });
    const i = cursorInfo([0.001, 0], { lat: 0, lng: 0, alt: 0 }, [newWaypoint([0, 0], 0, CMD.RETURN_TO_LAUNCH)]);
    expect(i.prev!.dist).toBeCloseTo(111.2, 0);
    expect(i.prev!.az).toBeCloseTo(0, 3);
    expect(i.home).toBeCloseTo(111.2, 0);
  });
  it('parses coordinates, not place names', () => {
    expect(parseLatLng('1.2966, 103.7764')).toEqual([1.2966, 103.7764]);
    expect(parseLatLng('  -33.9 151.2 ')).toEqual([-33.9, 151.2]);
    expect(parseLatLng('Sydney')).toBeNull();
    expect(parseLatLng('95, 10')).toBeNull();
  });
  it('initial view: planned home, then last view (lat ~0 = world zoom), then the course', () => {
    expect(initialView({ lat: 1, lng: 2, alt: 0 }, { lat: 5, lng: 5, zoom: 12 })).toEqual({ lat: 1, lng: 2, zoom: 16 });
    expect(initialView(null, { lat: 5, lng: 5, zoom: 12 })).toEqual({ lat: 5, lng: 5, zoom: 12 });
    expect(initialView(null, { lat: 0.01, lng: 5, zoom: 12 })).toEqual({ lat: 0.01, lng: 5, zoom: 3 });
    expect(initialView(null, null)).toBe('course');
  });
});

import { DEFAULT_PLACE, findPlace } from './waypoints';

describe('logic 12: named places', () => {
  it('finds a shortcut regardless of case and spaces, nothing else', () => {
    const places = { RMK: [DEFAULT_PLACE.lat, DEFAULT_PLACE.lng] as LatLng, 'Test pond': [13.35, 80.14] as LatLng };
    expect(findPlace(' rmk ', places)).toEqual([13.3568164, 80.1422648]);
    expect(findPlace('TEST POND', places)).toEqual([13.35, 80.14]);
    expect(findPlace('Kavaraipettai', places)).toBeNull();
  });
});

import { CMD_SET, columnNames, hasPosition, setCommand, takesPosition } from './waypoints';
import { MAVCMD } from './mavcmd';

describe('logic 13: MP command lists', () => {
  it('USV gets ArduRover\'s 42 commands, UAV ArduCopter\'s 50, in MP order', () => {
    expect(MAVCMD[CMD_SET.USV]).toHaveLength(42);
    expect(MAVCMD[CMD_SET.UAV]).toHaveLength(50);
    expect(MAVCMD.APRover.slice(0, 3).map((d) => d[0])).toEqual(['WAYPOINT', 'RETURN_TO_LAUNCH', 'DELAY']);
    expect(MAVCMD.APRover.some((d) => d[0] === 'TAKEOFF')).toBe(false);
    expect(MAVCMD.AC2.some((d) => d[0] === 'TAKEOFF')).toBe(true);
  });
  it('column names come from mavcmd.xml, with units', () => {
    expect(columnNames(16)).toEqual(['Delay', '', '', '', 'Lat', 'Long', 'Alt (m)']);
    expect(columnNames(177)).toEqual(['WP #', 'Repeat#', '', '', '', '', '']); // DO_JUMP
    expect(columnNames(9999)[0]).toBe('setme');
    expect(takesPosition(16)).toBe(true);
    expect(takesPosition(177)).toBe(false);
  });
  it('changing the command zeroes the columns the new one does not use', () => {
    const w = { ...newWaypoint([13.35, 80.14], 10), p: [5, 6, 7, 8] as [number, number, number, number] };
    setCommand(w, 183); // DO_SET_SERVO: Ser No, PWM
    expect(w).toMatchObject({ cmd: 183, p: [5, 6, 0, 0], lat: 0, lng: 0, alt: 0 });
    expect(hasPosition(w)).toBe(false);
  });
  it('ROI is not on the route', () => {
    expect(hasPosition(newWaypoint([13.35, 80.14], 0, CMD.DO_SET_ROI))).toBe(false);
  });
});
