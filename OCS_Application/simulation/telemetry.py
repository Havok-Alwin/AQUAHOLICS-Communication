# ============================================================
# AQUAHOLICS ROBOTX 2026
# SIMULATED VEHICLE TELEMETRY
#
# Returns fake position/speed/heading for local MQTT testing.
# Replace with real vehicle data adapters for competition.
# ============================================================

from robotx import rx_common_pb2


SIMULATED_VEHICLES = {

    "USV1": {
        "vehicle_type": rx_common_pb2.TYPE_USV,
        "latitude": 1.28090,
        "longitude": 103.85548,
        "speed": 1.4,
        "heading": 123.0,
    },

    "UAV1": {
        "vehicle_type": rx_common_pb2.TYPE_UAV,
        "latitude": 1.28070,
        "longitude": 103.85531,
        "speed": 6.5,
        "heading": 160.0,
    },

}


def get_telemetry(vehicle_id):
    """Return simulated telemetry for a vehicle, or None if unknown."""
    return SIMULATED_VEHICLES.get(vehicle_id)
