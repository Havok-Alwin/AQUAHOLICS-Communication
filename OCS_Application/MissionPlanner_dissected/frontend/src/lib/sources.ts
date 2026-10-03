import { STALE_AFTER_MS, VEHICLES, type VehicleId } from './config';
import { createSource, type Source } from './source';

// The sources on the display. Transports (link B WebSocket, link C SSE) are
// attached in later phases; until then every source stays OFFLINE.
export const vehicleBackend = createSource('Vehicle backend', STALE_AFTER_MS.vehicleBackend);
export const ocs = createSource('OCS', STALE_AFTER_MS.ocs);

export const vehicles: Record<VehicleId, Source> = Object.fromEntries(
  VEHICLES.map((id) => [id, createSource(id, STALE_AFTER_MS.vehicle)]),
) as Record<VehicleId, Source>;
