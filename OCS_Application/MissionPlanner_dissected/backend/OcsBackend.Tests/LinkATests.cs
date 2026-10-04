// Link A: robot-state rules (same as the frontend), heartbeat content, and the /linka endpoint.
using System.Net.WebSockets;
using System.Text.Json;
using System.Text.RegularExpressions;
using MissionPlanner;

namespace Ocs.Backend.Tests;

public class RobotStateTests
{
    [Fact]
    public void AutonomousModesMatchTheFrontend()
    {
        string? path = null;
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null && path == null; dir = dir.Parent)
        {
            var p = Path.Combine(dir.FullName, "frontend/src/lib/autonomy.ts");
            if (File.Exists(p)) path = p;
        }
        var ts = File.ReadAllText(path!);
        foreach (var kind in new[] { VehicleKind.USV, VehicleKind.UAV })
        {
            var list = Regex.Match(ts, kind + @": new Set\(\[(.*?)\]\)").Groups[1].Value;
            var modes = Regex.Matches(list, "'([^']+)'").Select(m => m.Groups[1].Value).ToHashSet();
            Assert.Equal(modes.Order(), RobotState.AutonomousModes[kind].Order());
        }
    }

    [Theory]
    // MP's names (parameter metadata) differ in spelling between Rover and Copter.
    [InlineData(VehicleKind.USV, true, "SmartRTL", "AUTO")]
    [InlineData(VehicleKind.USV, true, "Hold", "AUTO")]
    [InlineData(VehicleKind.USV, true, "Manual", "MANUAL")]
    [InlineData(VehicleKind.UAV, true, "Smart_RTL", "AUTO")]
    [InlineData(VehicleKind.UAV, true, "Auto RTL", "AUTO")]
    [InlineData(VehicleKind.UAV, true, "Loiter", "MANUAL")]  // Copter LOITER takes pilot input
    [InlineData(VehicleKind.UAV, false, "Auto", "UNKNOWN")]  // disarmed is not KILLED
    [InlineData(VehicleKind.USV, true, null, "UNKNOWN")]
    public void StateFollowsArmedAndMode(VehicleKind kind, bool armed, string? mode, string expected) =>
        Assert.Equal(expected, RobotState.Of(kind, armed, mode));

    [Theory]
    [InlineData(1, "GROUNDED")]
    [InlineData(2, "AIRBORNE")]
    [InlineData(3, "AIRBORNE")]
    [InlineData(4, "AIRBORNE")]
    [InlineData(0, "UNKNOWN")]
    public void FlightPhaseFromLandedState(byte landed, string expected) =>
        Assert.Equal(expected, RobotState.FlightPhase(landed));
}

public class LinkAMessageTests
{
    private static CurrentState NewState()
    {
        StreamRates.DisableMpRerequest();
        return new MAVLinkInterface().MAV.cs;
    }

    private static void SetLandedState(CurrentState cs, byte value) =>
        typeof(CurrentState).GetProperty("landed_state")!.GetSetMethod(nonPublic: true)!.Invoke(cs, new object[] { value });

    private static JsonElement Parse(byte[] json) => JsonDocument.Parse(json).RootElement;

    private static string[] Missing(JsonElement hb) =>
        hb.GetProperty("missing").EnumerateArray().Select(e => e.GetString()!).ToArray();

    [Fact]
    public void UsvHeartbeatWithAFixHasPositionAndNoAltitude()
    {
        var cs = NewState();
        cs.armed = true;
        cs.mode = "Auto";
        cs.gpsstatus = 3;
        cs.lat = 1.2966;
        cs.lng = 103.7764;
        cs.roll = 1.5f;

        var hb = Parse(LinkAMessages.Heartbeat("USV1", 7, cs, null, DateTime.UtcNow));

        Assert.Equal("hb", hb.GetProperty("ch").GetString());
        Assert.Equal("USV", hb.GetProperty("type").GetString());
        Assert.Equal("AUTO", hb.GetProperty("state").GetString());
        Assert.Equal(1.2966, hb.GetProperty("lat").GetDouble());
        Assert.Equal(1.5, hb.GetProperty("roll_deg").GetDouble());
        Assert.False(hb.TryGetProperty("altitude_hae_m", out _));  // not applicable to a USV
        Assert.False(hb.TryGetProperty("flight_phase", out _));
        Assert.False(hb.TryGetProperty("depth_m", out _));
        Assert.Empty(Missing(hb));
    }

    [Fact]
    public void NoFixMeansNoPositionNeverZeroZero()
    {
        var cs = NewState();
        cs.gpsstatus = 1;
        cs.lat = 1.2966;
        cs.lng = 103.7764;

        var hb = Parse(LinkAMessages.Heartbeat("USV1", 7, cs, null, DateTime.UtcNow));

        Assert.False(hb.TryGetProperty("lat", out _));
        Assert.Contains("position", Missing(hb));
    }

    [Fact]
    public void UavAltitudeIsTheEllipsoidHeightFromARecentGpsRawInt()
    {
        var cs = NewState();
        cs.gpsstatus = 3;
        cs.lat = 1.2969;
        cs.lng = 103.7768;
        SetLandedState(cs, 2);
        var now = DateTime.UtcNow;

        var hb = Parse(LinkAMessages.Heartbeat("UAV1", 7, cs, new LinkAMessages.GpsRaw(3, 23456, now), now));
        Assert.Equal(23.456, hb.GetProperty("altitude_hae_m").GetDouble());
        Assert.Equal("AIRBORNE", hb.GetProperty("flight_phase").GetString());
        Assert.Empty(Missing(hb));

        // Old GPS_RAW_INT, no ellipsoid height (MAVLink 1), or no 3D fix: left out, named in missing.
        foreach (var gps in new LinkAMessages.GpsRaw?[] { new(3, 23456, now.AddSeconds(-3)), new(3, 0, now), new(2, 23456, now), null })
        {
            var h = Parse(LinkAMessages.Heartbeat("UAV1", 7, cs, gps, now));
            Assert.False(h.TryGetProperty("altitude_hae_m", out _));
            Assert.Contains("altitude_hae_m", Missing(h));
        }
    }

    [Fact]
    public void UnknownFlightPhaseIsLeftOut()
    {
        var cs = NewState();
        SetLandedState(cs, 0);
        var hb = Parse(LinkAMessages.Heartbeat("UAV1", 7, cs, null, DateTime.UtcNow));
        Assert.False(hb.TryGetProperty("flight_phase", out _));
        Assert.Contains("flight_phase", Missing(hb));
    }
}

public sealed class LinkAServerTests : IAsyncLifetime
{
    private readonly FakeVehicle _vehicle = new(sysid: 1);
    private readonly CancellationTokenSource _stop = new();
    private VehicleLink _link = null!;
    private LinkBHub _hubB = null!;
    private LinkAHub _hubA = null!;
    private Microsoft.AspNetCore.Builder.WebApplication _app = null!;
    private Thread _thread = null!;
    private string _url = "";

    public async Task InitializeAsync()
    {
        StreamRates.DisableMpRerequest();
        var config = new VehicleLinkConfig("USV1", "fake", 57600, 1)
        {
            ConnectTimeout = TimeSpan.FromSeconds(3),
            LostAfter = TimeSpan.FromMilliseconds(300),
        };
        _link = new VehicleLink(config, _ =>
        {
            var port = new FakeSerial(open: false);
            _vehicle.Port = port;
            return port;
        });
        _hubB = new LinkBHub(new[] { _link });
        _hubA = new LinkAHub(new[] { _link }, hbPeriod: TimeSpan.FromMilliseconds(100));
        _app = LinkBServer.Build(_hubB, "http://127.0.0.1:0", null, _stop.Token, _hubA);
        await _app.StartAsync();
        _url = _app.Urls.First();
        _thread = new Thread(() => _link.Run(_stop.Token)) { IsBackground = true };
        _thread.Start();
    }

    [Fact]
    public async Task HeartbeatsFlowOnlyWhileTheVehicleIsLive()
    {
        using var ws = new ClientWebSocket();
        await ws.ConnectAsync(new Uri("ws" + _url[4..] + LinkAMessages.Path), CancellationToken.None);
        var received = new List<(DateTime at, JsonElement msg)>();
        var loop = Task.Run(async () =>
        {
            var buffer = new byte[1 << 16];
            try
            {
                while (ws.State == WebSocketState.Open)
                {
                    var r = await ws.ReceiveAsync(buffer, CancellationToken.None);
                    if (r.MessageType == WebSocketMessageType.Close) return;
                    var msg = JsonDocument.Parse(buffer.AsMemory(0, r.Count)).RootElement.Clone();
                    lock (received) received.Add((DateTime.UtcNow, msg));
                }
            }
            catch (WebSocketException) { }
        });
        List<(DateTime at, JsonElement msg)> Of(string ch) { lock (received) return received.Where(m => m.msg.GetProperty("ch").GetString() == ch).ToList(); }
        void WaitFor(Func<bool> c, string what, double s = 10)
        {
            var until = DateTime.UtcNow.AddSeconds(s);
            while (!c()) { if (DateTime.UtcNow > until) throw new TimeoutException(what); Thread.Sleep(10); }
        }

        WaitFor(() => Of("backend").Any(), "backend message");
        WaitFor(() => Of("hb").Count >= 3, "hb");
        var hb = Of("hb").Last().msg;
        Assert.Equal("USV1", hb.GetProperty("vehicle").GetString());
        Assert.Equal("USV", hb.GetProperty("type").GetString());
        Assert.Contains("position", hb.GetProperty("missing").EnumerateArray().Select(e => e.GetString()));  // fake has no GPS

        _vehicle.Sending = false;
        WaitFor(() => _link.State == LinkState.Lost, "LOST", 3);
        var lostAt = DateTime.UtcNow;
        Thread.Sleep(800);
        Assert.DoesNotContain(Of("hb"), m => m.at > lostAt.AddMilliseconds(150));

        await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None);
        await loop;
    }

    public async Task DisposeAsync()
    {
        _stop.Cancel();
        await _app.StopAsync();
        _thread.Join(TimeSpan.FromSeconds(10));
        _hubA.Dispose();
        _hubB.Dispose();
        _vehicle.Dispose();
    }
}
