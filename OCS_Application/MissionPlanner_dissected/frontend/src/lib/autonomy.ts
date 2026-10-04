import type { VehicleType } from './config';
import type { CurrentStateFields } from './currentState';

// Competition view of a vehicle: the RobotX heartbeat's RobotState and FlightPhase,
// derived from CurrentState. PROVISIONAL: confirm with the team. The link A heartbeat uses the
// same rules, ported in backend/OcsBackend/RobotState.cs; a backend test checks the mode lists match.

export type RobotState = 'AUTO' | 'MANUAL' | 'KILLED' | 'UNKNOWN';
export type FlightPhase = 'AIRBORNE' | 'GROUNDED' | 'UNKNOWN';

// ArduPilot modes in which the autopilot, not a pilot, is in control, compared after
// normalizeMode: MP's names come from the parameter metadata and differ in spelling
// ("SmartRTL" on Rover, "Smart_RTL" and "Auto RTL" on Copter).
// Rover LOITER/HOLD are autonomous station-keeping. Copter LOITER/POSHOLD take pilot input.
const AUTONOMOUS_MODES: Record<VehicleType, ReadonlySet<string>> = {
  USV: new Set(['AUTO', 'GUIDED', 'RTL', 'SMARTRTL', 'HOLD', 'LOITER', 'CIRCLE', 'DOCK', 'FOLLOW']),
  UAV: new Set(['AUTO', 'GUIDED', 'RTL', 'SMARTRTL', 'LAND', 'CIRCLE', 'BRAKE', 'AUTORTL', 'FOLLOW']),
};

/** Upper case, letters and digits only: 'Smart_RTL', 'SmartRTL' -> 'SMARTRTL'. */
export const normalizeMode = (mode: string): string => mode.toUpperCase().replace(/[^A-Z0-9]/g, '');

/**
 * KILLED needs the e-stop signal, which is not wired yet, so it is never returned here.
 * Disarmed is not KILLED: it reports UNKNOWN, and the card shows DISARMED.
 */
export function robotState(type: VehicleType, cs: Partial<CurrentStateFields>): RobotState {
  if (cs.armed === undefined || cs.mode === undefined) return 'UNKNOWN';
  if (!cs.armed) return 'UNKNOWN';
  return AUTONOMOUS_MODES[type].has(normalizeMode(cs.mode)) ? 'AUTO' : 'MANUAL';
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
