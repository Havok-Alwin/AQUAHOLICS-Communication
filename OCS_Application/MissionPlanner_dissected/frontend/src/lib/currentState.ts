// Logic 1: the CurrentState fields the SLOW channel carries.
// Names and types match MissionPlanner.CurrentState in MissionPlanner.ArduPilot.dll (1.3.x IL),
// so the backend can serialize them by name with no renaming.
// Values come in whatever units CurrentState applies (defaults: m, m/s). Units are logic 10.
// roll/pitch/yaw are not here: they come on the FAST channel (logic 0).

export interface CurrentStateFields {
  // state
  armed: boolean;
  mode: string;
  failsafe: boolean;
  safetyactive: boolean;
  prearmstatus: boolean;
  ekfstatus: number; // float32
  landed_state: number; // uint8, MAV_LANDED_STATE. Not an MP binding: needed for the UAV flight phase
  // motion
  groundspeed: number; // float32
  airspeed: number; // float32
  verticalspeed: number; // float32
  groundcourse: number; // float32
  turnrate: number; // float32
  // position
  lat: number; // float64
  lng: number; // float64
  alt: number; // float32
  HomeAlt: number; // float64
  DistToHome: number; // float32
  // navigation targets
  wpno: number; // float32
  wp_dist: number; // float32
  nav_bearing: number; // float32
  nav_roll: number; // float32
  nav_pitch: number; // float32
  targetalt: number; // float32
  targetairspeed: number; // float32
  xtrack_error: number; // float32
  // power
  battery_voltage: number; // float64
  battery_remaining: number; // int32, %
  current: number; // float64
  // GPS
  gpsstatus: number; // float32, GPS_FIX_TYPE
  satcount: number; // float32
  gpshdop: number; // float32
  // link and health
  linkqualitygcs: number; // uint16, %
  load: number; // float32
  vibex: number; // float32
  vibey: number; // float32
  vibez: number; // float32
  // messages
  messageHigh: string;
  messageHighSeverity: number; // MAV_SEVERITY 0..7
}

export type CurrentStateField = keyof CurrentStateFields;
