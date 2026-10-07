// Phase 3: mission upload/download through VehicleLink against the scripted vehicle (real time,
// short timeouts), the same recipe as VehicleCommandTests.cs (logic 9).
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;

namespace Ocs.Backend.Tests;

public sealed class MissionCommandTests : IDisposable
{
    private readonly FakeVehicle _vehicle = new(sysid: 1);
    private readonly CancellationTokenSource _stop = new();
    private readonly VehicleLink _link;
    private readonly List<MissionUpdate> _updates = new();
    private readonly List<MissionResult> _results = new();
    private readonly Thread _thread;

    public MissionCommandTests()
    {
        StreamRates.DisableMpRerequest();
        var config = new VehicleLinkConfig("USV1", "fake", 57600, 1)
        {
            ConnectTimeout = TimeSpan.FromSeconds(3),
            LostAfter = TimeSpan.FromMilliseconds(300),
            CommandTimeout = TimeSpan.FromMilliseconds(200),
        };
        _link = new VehicleLink(config, _ =>
        {
            var port = new FakeSerial(open: false);
            _vehicle.Port = port;
            return port;
        });
        _link.MissionUpdated += (_, u) => { lock (_updates) _updates.Add(u); };
        _link.MissionResultReady += (_, r) => { lock (_results) _results.Add(r); };
        _thread = new Thread(() => _link.Run(_stop.Token)) { IsBackground = true };
        _thread.Start();
        WaitFor(() => _link.State == LinkState.Live, "LIVE");
    }

    private static void WaitFor(Func<bool> condition, string what, double seconds = 10)
    {
        var until = DateTime.UtcNow.AddSeconds(seconds);
        while (!condition())
        {
            if (DateTime.UtcNow > until)
                throw new TimeoutException($"timed out waiting for: {what}");
            Thread.Sleep(10);
        }
    }

    private List<MissionUpdate> Updates(string id) { lock (_updates) return _updates.Where(u => u.Id == id).ToList(); }

    private MissionUpdate Final(string id, double seconds = 5)
    {
        WaitFor(() => Updates(id).Any(u => u.Status is not "sent" and not "progress"), $"final status of {id}", seconds);
        return Updates(id).Last();
    }

    private static MissionItem Wp(ushort seq, double lat, double lng, float alt, ushort cmd = 16, byte frame = 3) =>
        new(seq, frame, cmd, 0, 0, 0, 0, lat, lng, alt);

    private static MAVLink.mavlink_mission_item_int_t ToWire(MissionItem w) => new()
    {
        seq = w.Seq, frame = w.Frame, command = w.Cmd,
        param1 = w.P1, param2 = w.P2, param3 = w.P3, param4 = w.P4,
        x = (int)Math.Round(w.Lat * 1e7), y = (int)Math.Round(w.Lng * 1e7), z = w.Alt,
        current = (byte)(w.Seq == 0 ? 1 : 0), autocontinue = 1,
    };

    [Fact]
    public void WriteIsSentThenAccepted()
    {
        var items = new[] { Wp(0, 13.35, 80.14, 0, cmd: 16, frame: 0), Wp(1, 13.351, 80.141, 10) };
        _link.SubmitMission(new MissionJob("w1", "mission_write", items));

        var final = Final("w1");
        Assert.Equal(new[] { "sent", "progress", "progress", "accepted" }, Updates("w1").Select(u => u.Status));
        Assert.Equal(2, final.Total);
        Assert.Equal(2, final.Current);
        Assert.Equal(2, _vehicle.MissionWritten.Count);
        Assert.Equal(items[0].Seq, _vehicle.MissionWritten[0].seq);
        Assert.Equal((int)Math.Round(items[1].Lat * 1e7), _vehicle.MissionWritten[1].x);
        Assert.Equal((int)Math.Round(items[1].Lng * 1e7), _vehicle.MissionWritten[1].y);
    }

    [Fact]
    public void WriteOfAnEmptyMissionIsAcceptedAtOnce()
    {
        _link.SubmitMission(new MissionJob("w0", "mission_write", Array.Empty<MissionItem>()));

        var final = Final("w0");
        Assert.Equal("accepted", final.Status);
        Assert.Equal(0, final.Total);
        Assert.Empty(_vehicle.MissionWritten);
    }

    [Fact]
    public void RefusalIsReportedWithTheVehiclesResult()
    {
        _vehicle.RejectWriteAfter = 0;  // deny as soon as the first item arrives
        var items = new[] { Wp(0, 13.35, 80.14, 0) };
        _link.SubmitMission(new MissionJob("w1", "mission_write", items));

        var final = Final("w1");
        Assert.Equal("rejected", final.Status);
        Assert.Contains("DENIED", final.Detail);
    }

    [Fact]
    public void NoAnswerIsRetriedThenTimesOut()
    {
        _vehicle.MissionResponding = false;
        var items = new[] { Wp(0, 13.35, 80.14, 0) };
        _link.SubmitMission(new MissionJob("w1", "mission_write", items));

        var final = Final("w1", seconds: 5);
        Assert.Equal("timeout", final.Status);
    }

    [Fact]
    public void AnOutOfOrderRequestIsServedWithoutBreakingTheTransfer()
    {
        var items = new[] { Wp(0, 13.35, 80.14, 0, frame: 0), Wp(1, 13.351, 80.141, 10), Wp(2, 13.352, 80.142, 12) };
        _link.SubmitMission(new MissionJob("w1", "mission_write", items));

        WaitFor(() => _vehicle.MissionWritten.Count >= 2, "2 items received");
        _vehicle.RequestMissionItem(0);  // the vehicle re-asks for an item it already has

        var final = Final("w1");
        Assert.Equal("accepted", final.Status);
        Assert.Equal(3, _vehicle.MissionWritten.Count);
    }

    [Fact]
    public void ReadReturnsTheVehiclesMission()
    {
        var items = new[] { Wp(0, 13.35, 80.14, 0, frame: 0), Wp(1, 13.351, 80.141, 10) };
        _vehicle.MissionToDownload = items.Select(ToWire).ToList();

        _link.SubmitMission(new MissionJob("r1", "mission_read"));

        var final = Final("r1");
        Assert.Equal("accepted", final.Status);
        Assert.Equal(2, final.Total);
        var result = Assert.Single(_results);
        Assert.Equal(2, result.Items.Count);
        Assert.Equal((byte)0, result.Items[0].Frame);  // home
        Assert.Equal(items[1].Cmd, result.Items[1].Cmd);
        Assert.Equal(items[1].Lat, result.Items[1].Lat, 5);
        Assert.Equal(items[1].Lng, result.Items[1].Lng, 5);
        Assert.Equal(items[1].Alt, result.Items[1].Alt, 3);
    }

    [Fact]
    public void ReadOfAnEmptyMissionReturnsNoItems()
    {
        _vehicle.MissionToDownload = new();
        _link.SubmitMission(new MissionJob("r0", "mission_read"));

        var final = Final("r0");
        Assert.Equal("accepted", final.Status);
        Assert.Equal(0, final.Total);
        Assert.Empty(Assert.Single(_results).Items);
    }

    [Fact]
    public void WriteThenReadRoundTripReturnsTheSameItems()
    {
        var items = new[] { Wp(0, 13.35, 80.14, 0, frame: 0), Wp(1, 13.351, 80.141, 10), Wp(2, 13.352, 80.142, 12) };
        _link.SubmitMission(new MissionJob("w1", "mission_write", items));
        Assert.Equal("accepted", Final("w1").Status);

        // The write landed in the vehicle's own bookkeeping: serve it back for the read.
        _vehicle.MissionToDownload = _vehicle.MissionWritten.ToList();
        _link.SubmitMission(new MissionJob("r1", "mission_read"));
        Assert.Equal("accepted", Final("r1").Status);

        var result = Assert.Single(_results);
        Assert.Equal(items.Length, result.Items.Count);
        for (var i = 0; i < items.Length; i++)
        {
            Assert.Equal(items[i].Cmd, result.Items[i].Cmd);
            Assert.Equal(items[i].Lat, result.Items[i].Lat, 5);
            Assert.Equal(items[i].Lng, result.Items[i].Lng, 5);
            Assert.Equal(items[i].Alt, result.Items[i].Alt, 3);
        }
    }

    [Fact]
    public void OneMissionAtATime()
    {
        _vehicle.MissionResponding = false;
        _link.SubmitMission(new MissionJob("w1", "mission_write", new[] { Wp(0, 1, 1, 0) }));
        WaitFor(() => Updates("w1").Any(), "w1 sent");
        _link.SubmitMission(new MissionJob("r1", "mission_read"));

        var busy = Final("r1");
        Assert.Equal("error", busy.Status);
        Assert.StartsWith("busy", busy.Detail);
    }

    [Fact]
    public void NothingIsSentWhileTheLinkIsNotLive()
    {
        _vehicle.Sending = false;
        WaitFor(() => _link.State == LinkState.Lost, "LOST");

        _link.SubmitMission(new MissionJob("r1", "mission_read"));

        var final = Final("r1");
        Assert.Equal("error", final.Status);
        Assert.Contains("LOST", final.Detail);
    }

    public void Dispose()
    {
        _stop.Cancel();
        _thread.Join(TimeSpan.FromSeconds(10));
        _vehicle.Dispose();
    }
}

public sealed class LinkBMissionTests : IAsyncLifetime
{
    private readonly FakeVehicle _vehicle = new(sysid: 1);
    private readonly CancellationTokenSource _stop = new();
    private VehicleLink _link = null!;
    private LinkBHub _hub = null!;
    private Microsoft.AspNetCore.Builder.WebApplication _app = null!;
    private Thread _thread = null!;
    private string _url = "";

    public async Task InitializeAsync()
    {
        StreamRates.DisableMpRerequest();
        var config = new VehicleLinkConfig("USV1", "fake", 57600, 1) { ConnectTimeout = TimeSpan.FromSeconds(3) };
        _link = new VehicleLink(config, _ =>
        {
            var port = new FakeSerial(open: false);
            _vehicle.Port = port;
            return port;
        });
        _hub = new LinkBHub(new[] { _link });
        _app = LinkBServer.Build(_hub, "http://127.0.0.1:0", null, _stop.Token);
        await _app.StartAsync();
        _url = _app.Urls.First();
        _thread = new Thread(() => _link.Run(_stop.Token)) { IsBackground = true };
        _thread.Start();
    }

    private async Task<ClientWebSocket> Connect()
    {
        var ws = new ClientWebSocket();
        await ws.ConnectAsync(new Uri("ws" + _url[4..] + LinkBServer.Path), CancellationToken.None);
        return ws;
    }

    [Fact]
    public async Task MissionWriteThenReadOverLinkBAreAnsweredWithMissionackAndMission()
    {
        var until = DateTime.UtcNow.AddSeconds(10);
        while (_link.State != LinkState.Live && DateTime.UtcNow < until)
            await Task.Delay(10);
        using var ws = await Connect();

        var received = new List<JsonElement>();
        var loop = Task.Run(async () =>
        {
            var buffer = new byte[1 << 16];
            try
            {
                while (ws.State == WebSocketState.Open)
                {
                    var r = await ws.ReceiveAsync(buffer, CancellationToken.None);
                    if (r.MessageType == WebSocketMessageType.Close) return;
                    lock (received) received.Add(JsonDocument.Parse(buffer.AsMemory(0, r.Count)).RootElement.Clone());
                }
            }
            catch (WebSocketException) { }
        });
        List<JsonElement> Of(string ch) { lock (received) return received.Where(m => m.GetProperty("ch").GetString() == ch).ToList(); }

        var write = Encoding.UTF8.GetBytes("""
            {"ch":"mission_write","id":"m1","vehicle":"USV1",
             "home":{"lat":13.35,"lng":80.14,"alt":0},
             "wps":[{"cmd":16,"frame":3,"p":[0,0,0,0],"lat":13.351,"lng":80.141,"alt":10}]}
            """);
        await ws.SendAsync(write, WebSocketMessageType.Text, true, CancellationToken.None);

        until = DateTime.UtcNow.AddSeconds(5);
        while (Of("missionack").Count(a => a.GetProperty("id").GetString() == "m1" && a.GetProperty("status").GetString() == "accepted") == 0
               && DateTime.UtcNow < until)
            await Task.Delay(10);
        var writeAcks = Of("missionack").Where(a => a.GetProperty("id").GetString() == "m1").ToList();
        Assert.Contains("sent", writeAcks.Select(a => a.GetProperty("status").GetString()));
        Assert.Equal("accepted", writeAcks.Last().GetProperty("status").GetString());

        // Serve the write back for the read, like a real vehicle would.
        _vehicle.MissionToDownload = _vehicle.MissionWritten.ToList();
        var read = Encoding.UTF8.GetBytes("""{"ch":"mission_read","id":"m2","vehicle":"USV1"}""");
        await ws.SendAsync(read, WebSocketMessageType.Text, true, CancellationToken.None);

        until = DateTime.UtcNow.AddSeconds(5);
        while (Of("mission").Count == 0 && DateTime.UtcNow < until)
            await Task.Delay(10);
        var mission = Of("mission").Last();
        Assert.Equal(13.35, mission.GetProperty("home").GetProperty("lat").GetDouble(), 3);
        var wps = mission.GetProperty("wps").EnumerateArray().ToList();
        Assert.Single(wps);
        Assert.Equal(16, wps[0].GetProperty("cmd").GetInt32());
        Assert.Equal(13.351, wps[0].GetProperty("lat").GetDouble(), 3);

        await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None);
        await loop;
    }

    public async Task DisposeAsync()
    {
        _stop.Cancel();
        await _app.StopAsync();
        _thread.Join(TimeSpan.FromSeconds(10));
        _hub.Dispose();
        _vehicle.Dispose();
    }
}
