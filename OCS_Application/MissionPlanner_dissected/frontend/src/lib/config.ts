// Vehicles shown on the operator display. Order = panel order.
export const VEHICLES = ['USV1', 'UAV1'] as const;
export type VehicleId = (typeof VEHICLES)[number];

// How long without an update before a source is shown as STALE.
// Provisional: revisit with logic 8 (connect / link-lost) and the radio data rate.
export const STALE_AFTER_MS = {
  vehicleBackend: 2000, // link B: status channel is 2 Hz
  vehicle: 2000, // per-vehicle MAVLink heartbeat via link B
  ocs: 3000, // link C: event-driven, ~1 Hz
} as const;
