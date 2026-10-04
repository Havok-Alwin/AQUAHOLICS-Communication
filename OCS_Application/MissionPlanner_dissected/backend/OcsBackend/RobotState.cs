// The RobotX view of a vehicle: Heartbeat.state (RobotState) and Heartbeat.flight_phase, derived
// from CurrentState. Port of frontend/src/lib/autonomy.ts: link A (heartbeats to RoboCommand) and
// the operator display must use the same rules. A test checks the mode lists against that file.
// PROVISIONAL, like autonomy.ts: confirm with the team.
using MissionPlanner;

namespace Ocs.Backend;

public enum VehicleKind { USV, UAV }

public static class RobotState
{
    // ArduPilot modes in which the autopilot, not a pilot, is in control, compared after
    // NormalizeMode: MP's names come from the parameter metadata and differ in spelling
    // ("SmartRTL" on Rover, "Smart_RTL" and "Auto RTL" on Copter).
    // Rover LOITER/HOLD are autonomous station-keeping. Copter LOITER/POSHOLD take pilot input.
    public static readonly IReadOnlyDictionary<VehicleKind, IReadOnlySet<string>> AutonomousModes =
        new Dictionary<VehicleKind, IReadOnlySet<string>>
        {
            [VehicleKind.USV] = new HashSet<string> { "AUTO", "GUIDED", "RTL", "SMARTRTL", "HOLD", "LOITER", "CIRCLE", "DOCK", "FOLLOW" },
            [VehicleKind.UAV] = new HashSet<string> { "AUTO", "GUIDED", "RTL", "SMARTRTL", "LAND", "CIRCLE", "BRAKE", "AUTORTL", "FOLLOW" },
        };

    /// <summary>Upper case, letters and digits only: "Smart_RTL", "SmartRTL" -> "SMARTRTL".</summary>
    public static string NormalizeMode(string mode) =>
        new string(mode.Where(char.IsAsciiLetterOrDigit).Select(char.ToUpperInvariant).ToArray());

    /// <summary>The vehicle kind from its name, as frontend/src/lib/config.ts VEHICLE_TYPE (USV1 -> USV).</summary>
    public static VehicleKind? KindOf(string vehicleName) =>
        vehicleName.StartsWith("USV", StringComparison.OrdinalIgnoreCase) ? VehicleKind.USV
        : vehicleName.StartsWith("UAV", StringComparison.OrdinalIgnoreCase) ? VehicleKind.UAV
        : null;

    /// <summary>
    /// "AUTO", "MANUAL" or "UNKNOWN". KILLED needs the e-stop signal, which is not wired yet, so it
    /// is never returned. Disarmed is not KILLED: it reports UNKNOWN (the card shows DISARMED).
    /// </summary>
    public static string Of(VehicleKind kind, bool armed, string? mode)
    {
        if (mode == null || !armed)
            return "UNKNOWN";
        return AutonomousModes[kind].Contains(NormalizeMode(mode)) ? "AUTO" : "MANUAL";
    }

    /// <summary>MAV_LANDED_STATE: 1 ON_GROUND, 2 IN_AIR, 3 TAKEOFF, 4 LANDING.</summary>
    public static string FlightPhase(byte landedState) => landedState switch
    {
        1 => "GROUNDED",
        2 or 3 or 4 => "AIRBORNE",
        _ => "UNKNOWN",
    };
}
