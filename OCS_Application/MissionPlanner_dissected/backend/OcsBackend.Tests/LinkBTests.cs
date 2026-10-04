// Link B: message shapes, the per-client mailbox, and the server end to end with a scripted vehicle.
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using MissionPlanner;

namespace Ocs.Backend.Tests;

public class LinkBMessageTests
{
    private static string FindUp(string relative)
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            var path = Path.Combine(dir.FullName, relative);
            if (File.Exists(path))
                return path;
        }
        throw new FileNotFoundException(relative);
    }

    [Fact]
    public void SlowFieldsMatchTheFrontendCurrentStateFields()
    {
        var ts = File.ReadAllText(FindUp("frontend/src/lib/currentState.ts"));
        var body = Regex.Match(ts, @"interface CurrentStateFields \{(.*?)\n\}", RegexOptions.Singleline).Groups[1].Value;
        var names = Regex.Matches(body, @"^\s+(\w+):", RegexOptions.Multiline).Select(m => m.Groups[1].Value);

        Assert.Equal(names, LinkBMessages.SlowFields);
    }

    [Fact]
    public void StatusCarriesTypedFieldsAndLeavesOutNonFiniteNumbers()
    {
        StreamRates.DisableMpRerequest();
        using var mav = new MAVLinkInterface();
        var cs = mav.MAV.cs;
        cs.armed = true;
        cs.mode = "AUTO";
        cs.ekfstatus = float.NaN;
        cs.groundspeed = 1.5f;

        var json = JsonDocument.Parse(LinkBMessages.Status("USV1", 1234, cs)).RootElement;

        Assert.Equal("status", json.GetProperty("ch").GetString());
        Assert.Equal("USV1", json.GetProperty("vehicle").GetString());
        Assert.Equal(1234, json.GetProperty("t").GetInt64());
        var fields = json.GetProperty("cs");
        Assert.True(fields.GetProperty("armed").GetBoolean());
        Assert.Equal("AUTO", fields.GetProperty("mode").GetString());
        Assert.Equal(1.5, fields.GetProperty("groundspeed").GetDouble());
        Assert.Equal(JsonValueKind.Number, fields.GetProperty("messageHighSeverity").ValueKind);  // enum as number
        Assert.False(fields.TryGetProperty("ekfstatus", out _));  // NaN: left out, shown as '—'
    }

    [Fact]
    public void OtherMessagesHaveTheFrontendShapes()
    {
        var att = JsonDocument.Parse(LinkBMessages.Attitude("UAV1", 5, 1.234, -2.345, 359.999)).RootElement;
        Assert.Equal("att", att.GetProperty("ch").GetString());
        Assert.Equal(1.23, att.GetProperty("r").GetDouble());
        Assert.Equal(-2.35, att.GetProperty("p").GetDouble());

        var st = JsonDocument.Parse(LinkBMessages.StatusText("UAV1", new StatusText(9, 2, "PreArm: x"))).RootElement;
        Assert.Equal("statustext", st.GetProperty("ch").GetString());
        Assert.Equal(9, st.GetProperty("t").GetInt64());
        Assert.Equal(2, st.GetProperty("severity").GetInt32());
        Assert.Equal("PreArm: x", st.GetProperty("text").GetString());

        var p = JsonDocument.Parse(LinkBMessages.Params("UAV1", new Dictionary<string, float> { ["BATT_CAPACITY"] = 5000 })).RootElement;
        Assert.Equal(5000, p.GetProperty("params").GetProperty("BATT_CAPACITY").GetDouble());
    }
}

public class LinkBMailboxTests
{
    private static byte[] B(string s) => Encoding.UTF8.GetBytes(s);
    private static string[] S(List<byte[]> all) => all.Select(Encoding.UTF8.GetString).ToArray();

    [Fact]
    public void SlowClientGetsOnlyTheLatestPerKeyButEveryLogEntryInOrder()
    {
        var client = new Mailbox(LinkBHub.MaxQueuedLog);
        for (var i = 0; i < 10; i++)
        {
            client.PostLatest("status:USV1", B($"status {i}"));
            client.PostLog(B($"log {i}"));
        }
        client.PostLatest("att:USV1", B("att"));

        var sent = S(client.Drain());

        Assert.Equal(Enumerable.Range(0, 10).Select(i => $"log {i}"), sent.Take(10));
        Assert.Equal(new[] { "status 9", "att" }, sent.Skip(10));
        Assert.Empty(client.Drain());
    }

    [Fact]
    public void LogBacklogBeyondTheLimitMarksTheClientForDisconnect()
    {
        var client = new Mailbox(LinkBHub.MaxQueuedLog);
        for (var i = 0; i < LinkBHub.MaxQueuedLog; i++)
            client.PostLog(B("x"));
        Assert.False(client.Overflowed);

        client.PostLog(B("one too many"));
        Assert.True(client.Overflowed);
    }
}

public sealed class LinkBServerTests : IAsyncLifetime
{
    private readonly FakeVehicle _vehicle = new(sysid: 1);
    private readonly CancellationTokenSource _stop = new();
    private readonly string _web = Directory.CreateTempSubdirectory("ocs-web").FullName;
    private VehicleLink _link = null!;
    private LinkBHub _hub = null!;
    private Microsoft.AspNetCore.Builder.WebApplication _app = null!;
    private Thread _thread = null!;
    private string _url = "";

    public async Task InitializeAsync()
    {
        StreamRates.DisableMpRerequest();
        File.WriteAllText(Path.Combine(_web, "index.html"), "<!doctype html><title>OCS</title>");
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
        _hub = new LinkBHub(new[] { _link }, slowPeriod: TimeSpan.FromMilliseconds(100));
        _app = LinkBServer.Build(_hub, "http://127.0.0.1:0", _web, _stop.Token);
        await _app.StartAsync();
        _url = _app.Urls.First();
        _thread = new Thread(() => _link.Run(_stop.Token)) { IsBackground = true };
        _thread.Start();
    }

    private sealed class Collector : IAsyncDisposable
    {
        private readonly ClientWebSocket _ws = new();
        private readonly List<(DateTime at, JsonElement msg)> _messages = new();
        private Task _loop = Task.CompletedTask;

        public static async Task<Collector> Connect(string url)
        {
            var c = new Collector();
            await c._ws.ConnectAsync(new Uri("ws" + url[4..] + LinkBServer.Path), CancellationToken.None);
            c._loop = c.Loop();
            return c;
        }

        private async Task Loop()
        {
            var buffer = new byte[1 << 16];
            try
            {
                while (_ws.State == WebSocketState.Open)
                {
                    var r = await _ws.ReceiveAsync(buffer, CancellationToken.None);
                    if (r.MessageType == WebSocketMessageType.Close)
                        return;
                    Assert.True(r.EndOfMessage);
                    var msg = JsonDocument.Parse(buffer.AsMemory(0, r.Count)).RootElement.Clone();
                    lock (_messages)
                        _messages.Add((DateTime.UtcNow, msg));
                }
            }
            catch (WebSocketException) { }
        }

        public List<(DateTime at, JsonElement msg)> Of(string ch)
        {
            lock (_messages)
                return _messages.Where(m => m.msg.GetProperty("ch").GetString() == ch).ToList();
        }

        public async ValueTask DisposeAsync()
        {
            try { await _ws.CloseAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None); }
            catch (WebSocketException) { }
            await _loop;
        }
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

    [Fact]
    public async Task StreamsLinkStateAttitudeStatusAndParams()
    {
        await using var c = await Collector.Connect(_url);

        WaitFor(() => c.Of("backend").Any(), "backend message");
        WaitFor(() => c.Of("backend").Any(m => m.msg.GetProperty("links").GetProperty("USV1").GetProperty("state").GetString() == "live"),
                "USV1 live in the backend message");
        WaitFor(() => c.Of("att").Any(), "att");
        WaitFor(() => c.Of("status").Count >= 3, "status");
        WaitFor(() => c.Of("params").Any(), "params");

        var att = c.Of("att").Last().msg;
        Assert.Equal(Math.Round(0.1 * 180 / Math.PI, 2), att.GetProperty("r").GetDouble());
        Assert.Single(c.Of("att"));  // the fake vehicle's attitude never changes: on change only
        var status = c.Of("status").Last().msg;
        Assert.Equal("USV1", status.GetProperty("vehicle").GetString());
        Assert.Contains(status.GetProperty("cs").GetProperty("armed").ValueKind, new[] { JsonValueKind.True, JsonValueKind.False });
        Assert.Equal(10000, c.Of("params").Last().msg.GetProperty("params").GetProperty("BATT_CAPACITY").GetDouble());
    }

    [Fact]
    public async Task StatusStopsWhileTheVehicleIsSilent()
    {
        await using var c = await Collector.Connect(_url);
        WaitFor(() => c.Of("status").Count >= 2, "status");

        _vehicle.Sending = false;
        WaitFor(() => _link.State == LinkState.Lost, "LOST", seconds: 3);
        var lostAt = DateTime.UtcNow;
        Thread.Sleep(1000);

        Assert.DoesNotContain(c.Of("status"), m => m.at > lostAt.AddMilliseconds(150));  // one in flight at most
        Assert.Contains(c.Of("backend"), m => m.msg.GetProperty("links").GetProperty("USV1").GetProperty("state").GetString() == "lost");

        _vehicle.Sending = true;
        WaitFor(() => c.Of("status").Any(m => m.at > lostAt.AddSeconds(1)), "status again once LIVE", seconds: 3);
    }

    [Fact]
    public async Task NewClientGetsTheMessageHistoryAndParams()
    {
        WaitFor(() => _link.State == LinkState.Live, "LIVE");
        _vehicle.SendStatusText(MAVLink.MAV_SEVERITY.WARNING, "first");
        _vehicle.SendStatusText(MAVLink.MAV_SEVERITY.INFO, "second");
        Thread.Sleep(300);

        await using var c = await Collector.Connect(_url);  // e.g. the kiosk page reloaded

        WaitFor(() => c.Of("statustext").Count >= 2, "history");
        Assert.Equal(new[] { "first", "second" }, c.Of("statustext").Select(m => m.msg.GetProperty("text").GetString()));
        WaitFor(() => c.Of("params").Any(), "params on connect");
    }

    [Fact]
    public async Task NewClientGetsTheCurrentAttitudeOfAStillVehicle()
    {
        WaitFor(() => _link.State == LinkState.Live, "LIVE");
        Thread.Sleep(300);  // attitude sent once already; it never changes

        await using var c = await Collector.Connect(_url);

        WaitFor(() => c.Of("att").Any(), "att on connect");
    }

    [Fact]
    public async Task ServesTheFrontendFiles()
    {
        using var http = new HttpClient();
        var html = await http.GetStringAsync(_url + "/");
        Assert.Contains("<title>OCS</title>", html);
        var notWs = await http.GetAsync(_url + LinkBServer.Path);
        Assert.Equal(System.Net.HttpStatusCode.BadRequest, notWs.StatusCode);
    }

    public async Task DisposeAsync()
    {
        _stop.Cancel();
        await _app.StopAsync();
        _thread.Join(TimeSpan.FromSeconds(10));
        _hub.Dispose();
        _vehicle.Dispose();
        Directory.Delete(_web, recursive: true);
    }
}
