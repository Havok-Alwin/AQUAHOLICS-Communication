// Logic 11: loop pacing. Every cadence in the frontend lives here, next to where it came from.
//
// MP (FlightData.mainloop, 1.3.x decompiled) runs one thread, sleeping 50 ms per pass (~20 Hz):
//   every pass   CurrentState housekeeping (UpdateCurrentSettings, self-gated 50 ms)   -> backend (logic 2)
//                updateBindingSource (UI push, gated 100 ms)                           -> UI_UPDATE_MIN_MS
//                battery low/critical check                                            -> logic 6, per snapshot
//   40 ms        record HUD to AVI                                                      not ported
//   75 ms        tuning graph (ZedGraph)                                                not ported
//   300 ms       log playback position                                                  not ported
//   300 ms       map: vehicle marker + track (FD_MapUpdateDelay, default 0.3 s;
//                every 2 s even while disconnected), track capped at NUM_tracklength 200 -> MAP_*
//   3 s          map auto-pan to the vehicle                                            -> MAP_AUTOPAN_MS
//   5 s          mission (waypoint) overlay refresh                                     -> MAP_MISSION_REFRESH_MS
//   5 s          transponder status                                                     not ported
//   markers      ADS-B / avoidance objects drawn only if seen in the last 30 s / 10 s   -> MAP_OBJECT_MAX_AGE_MS
//
// What we do differently: one requestAnimationFrame clock for drawing (logic 0) instead of a
// 50 ms sleep loop, and store-driven panels instead of polling. MP's map cadence is kept for the
// map because redrawing Leaflet layers every frame would waste the operator laptop's CPU.

/** Drawing: one rAF loop (frameClock.ts). Not a timer: follows the display refresh. */
export const FRAME = 'requestAnimationFrame' as const;

/** UI push gate for SLOW snapshots and logs (FlightData.updateBindingSource). Logic 2. */
export const UI_UPDATE_MIN_MS = 100;

/** Wall clock for staleness and "last update N s ago" text. */
export const CLOCK_TICK_MS = 250;

/** HUD fps / packet-rate counter. */
export const STATS_MS = 1000;

/** Link B WebSocket reconnect backoff: doubles from min to max while the backend is down. */
export const LINK_B_RECONNECT_MS = { min: 500, max: 5000 } as const;

/** Link B rates the backend is expected to send (stream-rate setup is logic 3, backend). */
export const LINK_B = {
  fastHz: 20, // attitude, on change
  slowHz: 2, // status snapshot
} as const;

/**
 * How long without an update before a source is STALE. Logic 8: a silent vehicle goes OFFLINE
 * first, ~1 s after its last packet, when the backend reports its link LOST (telemetry.ts); it
 * also stops sending that vehicle's SLOW. STALE is the safety net if updates stop for any other
 * reason (MP warns "No Data" only after 3 s).
 */
export const STALE_AFTER_MS = {
  vehicleBackend: 2000, // any link B message counts; the backend message alone comes at 1 Hz
  vehicle: 2000,
  ocs: 3000, // link C, ~1 Hz
} as const;

// --- map (to be built): MP's cadence ---------------------------------------------------------

/** Vehicle markers + track: FD_MapUpdateDelay default. */
export const MAP_UPDATE_MS = 300;
/** Same, while the vehicle link is down (MP keeps the map moving at 2 s). */
export const MAP_UPDATE_DISCONNECTED_MS = 2000;
/** NUM_tracklength default: points kept per vehicle track. */
export const MAP_TRACK_POINTS = 200;
/** Auto-pan to the selected vehicle. */
export const MAP_AUTOPAN_MS = 3000;
/** Mission (waypoint) overlay refresh. */
export const MAP_MISSION_REFRESH_MS = 5000;
/**
 * Tracked objects (MP hides ADS-B after 30 s, avoidance objects after 10 s). For Task 4 we do NOT
 * hide: after this age the moving object is drawn as STALE at its last known position, because
 * "stay > 10 m away" needs the last known position more than a clean map.
 */
export const MAP_OBJECT_MAX_AGE_MS = 10_000;
