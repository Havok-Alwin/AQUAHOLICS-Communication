// Vehicles shown on the operator display. Order = panel order.
export const VEHICLES = ['USV1', 'UAV1'] as const;
export type VehicleId = (typeof VEHICLES)[number];

export type VehicleType = 'USV' | 'UAV';
export const VEHICLE_TYPE: Record<VehicleId, VehicleType> = { USV1: 'USV', UAV1: 'UAV' };
