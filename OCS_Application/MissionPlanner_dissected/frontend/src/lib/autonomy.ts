import type { VehicleType } from './config';
import type { CurrentStateFields } from './currentState';

// Competition view of a vehicle: the RobotX heartbeat's RobotState and FlightPhase,
// derived from CurrentState. PROVISIONAL: confirm with the team. The same rules
// must later produce the link A heartbeat, so keep them in this one place.

export type RobotState = 'AUTO' | 'MANUAL' | 'KILLED' | 'UNKNOWN';
export type FlightPhase = 'AIRBORNE' | 'GROUNDED' | 'UNKNOWN';

// ArduPilot modes in which the autopilot, not a pilot, is in control.
// Rover LOITER/HOLD are autonomous station-keeping. Copter LOITER/POSHOLD take pilot input.
const AUTONOMOUS_MODES: Record<VehicleType, ReadonlySet<string>> = {
  USV: new Set(['AUTO', 'GUIDED', 'RTL', 'SMART_RTL', 'HOLD', 'LOITER', 'CIRCLE', 'DOCK', 'FOLLOW']),
  UAV: new Set(['AUTO', 'GUIDED', 'RTL', 'SMART_RTL', 'LAND', 'CIRCLE', 'BRAKE', 'AUTO_RTL', 'FOLLOW']),
};

/**
 * KILLED needs the e-stop signal, which is not wired yet, so it is never returned here.
 * Disarmed is not KILLED: it reports UNKNOWN, and the card shows DISARMED.
 */
export function robotState(type: VehicleType, cs: Partial<CurrentStateFields>): RobotState {
  if (cs.armed === undefined || cs.mode === undefined) return 'UNKNOWN';
  if (!cs.armed) return 'UNKNOWN';
  return AUTONOMOUS_MODES[type].has(cs.mode.toUpperCase()) ? 'AUTO' : 'MANUAL';
}

/** MAV_LANDED_STATE: 1 ON_GROUND, 2 IN_AIR, 3 TAKEOFF, 4 LANDING. */
export function flightPhase(cs: Partial<CurrentStateFields>): FlightPhase {
  switch (cs.landed_state) {
    case 1:
      return 'GROUNDED';
    case 2:
    case 3:
    case 4:
      return 'AIRBORNE';
    default:
      return 'UNKNOWN';
  }
}
