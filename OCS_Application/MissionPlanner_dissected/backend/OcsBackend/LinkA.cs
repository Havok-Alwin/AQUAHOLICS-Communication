// Link A: vehicle backend -> OCS (../main.py). The OCS turns each `hb` into the RobotX
// RxReport.heartbeat it publishes to RoboCommand (2 Hz per vehicle, handbook "Heartbeats and
// task reports"). WebSocket /linka on the backend's Kestrel server, JSON text frames.
//
//   hb       {ch:'hb', vehicle, t, type, state, lat, lng, spd_mps, heading_deg, roll_deg,
//             pitch_deg, altitude_hae_m, flight_phase, missing:[...]}     2 Hz, only while LIVE
//   backend  {ch:'backend', t, links:{USV1:{state, error}}}               1 Hz and on link change
//
// Rules:
// - hb only while the vehicle link is LIVE (logic 8): the OCS never gets old data as current, and
//   stops publishing that vehicle's heartbeat when hb stops.
// - A value that is not known is left out and named in `missing`, never sent as a made-up number:
//   position without a 3D GPS fix; altitude_hae_m (UAV) without a recent GPS_RAW_INT alt_ellipsoid
//   (MAVLink 2 only; CurrentState has no ellipsoid altitude, and AMSL differs from HAE by the geoid
//   height, several metres); flight_phase (UAV) while landed_state is unknown.
// - USV: no altitude, no flight phase (handbook: "where applicable"). depth_m is for UUVs: never sent.
// - state and flight_phase: RobotState.cs, the same rules as the operator display.
// - Units are SI (the CurrentState multipliers are 1, see CLAUDE.md), degrees, heading 0..360.
using System.Net.WebSockets;
using System.Text.Json;
using MissionPlanner;

namespace Ocs.Backend;

public static class LinkAMessages
{
    public const string Path = "/linka";

    /// <summary>GPS_RAW_INT fields CurrentState does not keep.</summary>
    public readonly record struct GpsRaw(byte FixType, int AltEllipsoidMm, DateTime ReceivedUtc);

    public static byte[] Heartbeat(string vehicle, long t, CurrentState cs, GpsRaw? gps, DateTime nowUtc)
    {
        var kind = RobotState.KindOf(vehicle);
        var missing = new List<string>();
        var buffer = new System.Buffers.ArrayBufferWriter<byte>(256);
        using (var w = new Utf8JsonWriter(buffer))
        {
            w.WriteStartObject();
            w.WriteString("ch", "hb");
            w.WriteString("vehicle", vehicle);
            w.WriteNumber("t", t);
            if (kind is { } k)
                w.WriteString("type", k.ToString());
            else
                missing.Add("type");
            w.WriteString("state", kind is { } k2 ? RobotState.Of(k2, cs.armed, cs.mode) : "UNKNOWN");

            if (cs.gpsstatus >= 3 && (cs.lat != 0 || cs.lng != 0) && double.IsFinite(cs.lat) && double.IsFinite(cs.lng))
            {
                w.WriteNumber("lat", cs.lat);
                w.WriteNumber("lng", cs.lng);
            }
            else
                missing.Add("position");

            Number(w, "spd_mps", cs.groundspeed, missing);
            Number(w, "heading_deg", cs.yaw, missing);
            Number(w, "roll_deg", cs.roll, missing);
            Number(w, "pitch_deg", cs.pitch, missing);

            if (kind == VehicleKind.UAV)
            {
                if (gps is { } g && nowUtc - g.ReceivedUtc < TimeSpan.FromSeconds(2) && g.FixType >= 3 && g.AltEllipsoidMm != 0)
                    w.WriteNumber("altitude_hae_m", Math.Round(g.AltEllipsoidMm / 1000.0, 3));
                else
                    missing.Add("altitude_hae_m");

                var phase = RobotState.FlightPhase(cs.landed_state);
                if (phase != "UNKNOWN")
                    w.WriteString("flight_phase", phase);
                else
                    missing.Add("flight_phase");
            }

            w.WriteStartArray("missing");
            foreach (var m in missing)
                w.WriteStringValue(m);
            w.WriteEndArray();
            w.WriteEndObject();
        }
        return buffer.WrittenSpan.ToArray();
    }

    private static void Number(Utf8JsonWriter w, string name, float value, List<string> missing)
    {
        if (float.IsFinite(value))
            w.WriteNumber(name, Math.Round(value, 3));
        else
            missing.Add(name);
    }
}

public sealed class LinkAHub : IDisposable
{
    private readonly IReadOnlyList<VehicleLink> _links;
    private readonly object _lock = new();
    private readonly List<Mailbox> _clients = new();
    private readonly Dictionary<string, LinkAMessages.GpsRaw> _gps = new();
    private readonly Timer _hbTimer, _backendTimer;

    public LinkAHub(IReadOnlyList<VehicleLink> links, TimeSpan? hbPeriod = null, TimeSpan? backendPeriod = null)
    {
        _links = links;
        foreach (var link in links)
        {
            link.PacketReceived += OnPacket;
            link.StateChanged += (_, _) => PostBackend();
        }
        var hb = hbPeriod ?? TimeSpan.FromMilliseconds(500);  // config.HEARTBEAT_HZ = 2
        var backend = backendPeriod ?? TimeSpan.FromSeconds(1);
        _hbTimer = new Timer(_ => PostHeartbeats(), null, hb, hb);
        _backendTimer = new Timer(_ => PostBackend(), null, backend, backend);
    }

    private void OnPacket(VehicleLink link, MAVLink.MAVLinkMessage msg)
    {
        if (msg.msgid != (uint)MAVLink.MAVLINK_MSG_ID.GPS_RAW_INT)
            return;
        var gps = msg.ToStructure<MAVLink.mavlink_gps_raw_int_t>();
        lock (_lock)
            _gps[link.Name] = new LinkAMessages.GpsRaw(gps.fix_type, gps.alt_ellipsoid, DateTime.UtcNow);
    }

    private void PostHeartbeats()
    {
        foreach (var link in _links)
        {
            var mav = link.Mav;
            if (link.State != LinkState.Live || mav == null)
                continue;
            LinkAMessages.GpsRaw? gps;
            lock (_lock)
                gps = _gps.TryGetValue(link.Name, out var g) ? g : null;
            byte[] msg;
            try { msg = LinkAMessages.Heartbeat(link.Name, LinkBMessages.Now(), mav.MAV.cs, gps, DateTime.UtcNow); }
            catch (Exception e) { Console.Error.WriteLine($"link A: {link.Name} heartbeat failed: {e.Message}"); continue; }
            Post($"hb:{link.Name}", msg);
        }
    }

    private void PostBackend() => Post("backend", LinkBMessages.Backend(LinkBMessages.Now(), _links));

    private void Post(string key, byte[] msg)
    {
        lock (_lock)
            foreach (var client in _clients)
                client.PostLatest(key, msg);
    }

    /// <summary>Serves the OCS until it closes or `stop` fires. Latest-wins: no backlog of old heartbeats.</summary>
    public async Task ServeAsync(WebSocket ws, CancellationToken stop)
    {
        var client = new Mailbox(maxLog: 0);
        lock (_lock)
        {
            client.PostLatest("backend", LinkBMessages.Backend(LinkBMessages.Now(), _links));
            _clients.Add(client);
        }
        try
        {
            await client.PumpAsync(ws, stop);
        }
        finally
        {
            lock (_lock)
                _clients.Remove(client);
        }
    }

    public void Dispose()
    {
        _hbTimer.Dispose();
        _backendTimer.Dispose();
    }
}
