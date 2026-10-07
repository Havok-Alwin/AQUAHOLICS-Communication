// Phase 3: handleLinkB's missionack / mission branches (MissionCommands.cs's backend messages).
import { get } from 'svelte/store';
import { describe, expect, it } from 'vitest';
import { handleLinkB, vehicleMission, vehicleMissionOp, type MissionAckMsg, type MissionMsg } from './telemetry';

describe('mission transfer messages (phase 3)', () => {
  it('missionack tracks sent -> progress -> accepted with the progress counters', () => {
    const sent: MissionAckMsg = {
      ch: 'missionack', vehicle: 'USV1', id: 'm1', cmd: 'mission_write',
      status: 'sent', detail: 'writing 2 item(s)', current: 0, total: 2, t: 1000,
    };
    handleLinkB(sent);
    expect(get(vehicleMissionOp.USV1)).toMatchObject({ id: 'm1', cmd: 'mission_write', status: 'sent' });

    handleLinkB({ ...sent, status: 'progress', detail: '1/2 uploaded', current: 1 });
    expect(get(vehicleMissionOp.USV1)).toMatchObject({ status: 'progress', current: 1, total: 2 });

    handleLinkB({ ...sent, status: 'accepted', detail: '2 item(s) written', current: 2 });
    expect(get(vehicleMissionOp.USV1)).toMatchObject({ status: 'accepted', current: 2, total: 2 });
  });

  it('missionack reports a rejection with the vehicle detail', () => {
    const rejected: MissionAckMsg = {
      ch: 'missionack', vehicle: 'UAV1', id: 'm2', cmd: 'mission_write',
      status: 'rejected', detail: 'MAV_MISSION_DENIED', t: 2000,
    };
    handleLinkB(rejected);
    expect(get(vehicleMissionOp.UAV1)).toMatchObject({ status: 'rejected', detail: 'MAV_MISSION_DENIED' });
  });

  it('mission sets the downloaded home and waypoints', () => {
    const msg: MissionMsg = {
      ch: 'mission',
      vehicle: 'UAV1',
      home: { lat: 13.35, lng: 80.14, alt: 0 },
      wps: [{ cmd: 16, lat: 13.351, lng: 80.141, alt: 10, p: [0, 0, 0, 0], frame: 3 }],
    };
    handleLinkB(msg);
    expect(get(vehicleMission.UAV1)).toEqual(msg);
  });

  it('mission with no home is passed through as null', () => {
    const msg: MissionMsg = { ch: 'mission', vehicle: 'USV1', home: null, wps: [] };
    handleLinkB(msg);
    expect(get(vehicleMission.USV1)).toEqual(msg);
  });
});
