// Logic 9: commands through VehicleLink against the scripted vehicle (real time, short timeouts).
using System.Net;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;

namespace Ocs.Backend.Tests;

public sealed class VehicleCommandTests : IDisposable
{
    private readonly FakeVehicle _vehicle = new(sysid: 1);
    private readonly CancellationTokenSource _stop = new();
    private readonly VehicleLink _link;
    private readonly List<CommandUpdate> _updates = new();
    private readonly Thread _thread;

    public VehicleCommandTests()
    {
        StreamRates.DisableMpRerequest();
        var config = new VehicleLinkConfig("USV1", "fake", 57600, 1)
        {
            ConnectTimeout = TimeSpan.FromSeconds(3),
            LostAfter = TimeSpan.FromMilliseconds(300),
            CommandTimeout = TimeSpan.FromMilliseconds(200),
            ArmTimeout = TimeSpan.FromMilliseconds(300),
        };
        _link = new VehicleLink(config, _ =>
        {
            var port = new FakeSerial(open: false);
            _vehicle.Port = port;
            return port;
        });
        _link.CommandUpdated += (_, u) => { lock (_updates) _updates.Add(u); };
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

    private List<CommandUpdate> Updates(string id) { lock (_updates) return _updates.Where(u => u.Id == id).ToList(); }

    private CommandUpdate Final(string id, double seconds = 5)
    {
        WaitFor(() => Updates(id).Any(u => u.Status != "sent"), $"final status of {id}", seconds);
        return Updates(id).Last();
    }

    // Arm/disarm and mode commands only: MP's Open() also sends a few COMMAND_LONGs (banner, version).
    private List<MAVLink.mavlink_command_long_t> Sent()
    {
        lock (_vehicle.Commands)
            return _vehicle.Commands.Where(c => c.command is (ushort)MAVLink.MAV_CMD.COMPONENT_ARM_DISARM
                                                 or (ushort)MAVLink.MAV_CMD.DO_SET_MODE).ToList();
    }

    [Fact]
    public void ModesOfTheConnectedFirmwareAreKnown()
    {
        // A boat: ArduRover, MP's MODE1 names.
        Assert.Contains("Auto", _link.Modes);
        Assert.Contains("Hold", _link.Modes);
        Assert.Contains("Manual", _link.Modes);
        Assert.DoesNotContain("AltHold", _link.Modes);  // a Copter mode
    }

    [Fact]
    public void ArmIsSentThenAcceptedAndTheVehicleArms()
    {
        _link.Submit(new VehicleCommand("a1", "arm"));

        var final = Final("a1");
        Assert.Equal(new[] { "sent", "accepted" }, Updates("a1").Select(u => u.Status));
        Assert.Equal("arm", final.Detail);
        var cmd = Assert.Single(Sent());
        Assert.Equal((ushort)MAVLink.MAV_CMD.COMPONENT_ARM_DISARM, cmd.command);
        Assert.Equal(1, cmd.param1);
        Assert.Equal(0, cmd.param2);  // never forced
        WaitFor(() => _link.Mav!.MAV.cs.armed, "armed in the heartbeat");

        _link.Submit(new VehicleCommand("d1", "disarm"));
        Assert.Equal("accepted", Final("d1").Status);
        Assert.Equal(0, Sent().Last().param1);
    }

    [Fact]
    public void RefusalIsReportedWithTheVehiclesResult()
    {
        _vehicle.CommandReply = FakeVehicle.Reply.Deny;
        _link.Submit(new VehicleCommand("a1", "arm"));

        var final = Final("a1");
        Assert.Equal("rejected", final.Status);
        Assert.Equal("DENIED", final.Detail);
        Assert.Single(Sent());  // a refusal is final: no retry
    }

    [Fact]
    public void NoAnswerIsRetriedLikeMpThenTimesOut()
    {
        _vehicle.CommandReply = FakeVehicle.Reply.Ignore;
        _link.Submit(new VehicleCommand("a1", "arm"));

        var final = Final("a1", seconds: 5);
        Assert.Equal("timeout", final.Status);
        // MP: first try + 3 retries, confirmation counting up.
        Assert.Equal(new byte[] { 0, 1, 2, 3 }, Sent().Select(c => c.confirmation));
    }

    [Fact]
    public void InProgressRestartsTheWaitAndStopsTheRetries()
    {
        // MP: IN_PROGRESS restarts the timeout once and no retry follows.
        _vehicle.CommandReply = FakeVehicle.Reply.InProgressThenAccept;
        _vehicle.InProgressMs = 200;  // within the restarted 300 ms arm timeout
        _link.Submit(new VehicleCommand("a1", "arm"));
        Assert.Equal("accepted", Final("a1").Status);
        Assert.Single(Sent());

        _vehicle.InProgressMs = 3000;  // final answer far too late
        _link.Submit(new VehicleCommand("a2", "disarm"));
        Assert.Equal("timeout", Final("a2").Status);
        Assert.Equal(2, Sent().Count);  // still one try each: no retries after IN_PROGRESS
    }

    [Fact]
    public void ModeChangeUsesTheFirmwareModeNumber()
    {
        _link.Submit(new VehicleCommand("m1", "mode", "Hold"));

        Assert.Equal("accepted", Final("m1").Status);
        var cmd = Assert.Single(Sent());
        Assert.Equal((ushort)MAVLink.MAV_CMD.DO_SET_MODE, cmd.command);
        Assert.Equal(1, cmd.param1);  // MAV_MODE_FLAG_CUSTOM_MODE_ENABLED
        Assert.Equal(4, cmd.param2);  // Rover HOLD
        WaitFor(() => _link.Mav!.MAV.cs.mode == "Hold", "mode Hold in the heartbeat");
    }

    [Fact]
    public void UnknownModeIsNeverSent()
    {
        _link.Submit(new VehicleCommand("m1", "mode", "AltHold"));  // Copter only

        var final = Final("m1");
        Assert.Equal("error", final.Status);
        Assert.Contains("unknown mode", final.Detail);
        Assert.Empty(Sent());
    }

    [Fact]
    public void OneCommandAtATime()
    {
        _vehicle.CommandReply = FakeVehicle.Reply.Ignore;
        _link.Submit(new VehicleCommand("a1", "arm"));
        WaitFor(() => Updates("a1").Any(), "a1 sent");
        _link.Submit(new VehicleCommand("m1", "mode", "Hold"));

        var busy = Final("m1");
        Assert.Equal("error", busy.Status);
        Assert.StartsWith("busy", busy.Detail);
    }

    [Fact]
    public void NothingIsSentWhileTheLinkIsNotLive()
    {
        _vehicle.Sending = false;
        WaitFor(() => _link.State == LinkState.Lost, "LOST");

        _link.Submit(new VehicleCommand("a1", "arm"));

        var final = Final("a1");
        Assert.Equal("error", final.Status);
        Assert.Contains("LOST", final.Detail);
        Assert.Empty(Sent());
    }

    public void Dispose()
    {
        _stop.Cancel();
        _thread.Join(TimeSpan.FromSeconds(10));
        _vehicle.Dispose();
    }
}

public sealed class LinkBCommandTests : IAsyncLifetime
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

    private async Task<ClientWebSocket> Connect(string? origin)
    {
        var ws = new ClientWebSocket();
        if (origin != null)
            ws.Options.SetRequestHeader("Origin", origin);
        await ws.ConnectAsync(new Uri("ws" + _url[4..] + LinkBServer.Path), CancellationToken.None);
        return ws;
    }

    [Fact]
    public async Task PagesFromOtherOriginsCannotConnect()
    {
        var refused = await Assert.ThrowsAsync<WebSocketException>(() => Connect("http://evil.example"));
        Assert.Contains("403", refused.Message);

        using var same = await Connect(_url);  // the page this server serves
        using var dev = await Connect("http://localhost:5173");  // the Vite dev server
        Assert.Equal(WebSocketState.Open, same.State);
        Assert.Equal(WebSocketState.Open, dev.State);
    }

    [Fact]
    public async Task CommandOverLinkBIsAnsweredWithCmdAcks()
    {
        var until = DateTime.UtcNow.AddSeconds(10);
        while (_link.State != LinkState.Live && DateTime.UtcNow < until)
            await Task.Delay(10);
        using var ws = await Connect(_url);

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

        var cmd = Encoding.UTF8.GetBytes("""{"ch":"cmd","id":"c1","vehicle":"USV1","cmd":"mode","mode":"Hold"}""");
        await ws.SendAsync(cmd, WebSocketMessageType.Text, true, CancellationToken.None);

        until = DateTime.UtcNow.AddSeconds(5);
        while (Of("cmdack").Count < 2 && DateTime.UtcNow < until)
            await Task.Delay(10);
        var acks = Of("cmdack");
        Assert.Equal(new[] { "sent", "accepted" }, acks.Select(a => a.GetProperty("status").GetString()));
        Assert.All(acks, a => Assert.Equal("c1", a.GetProperty("id").GetString()));
        Assert.Contains("Hold", Of("modes").Last().GetProperty("modes").EnumerateArray().Select(m => m.GetString()));

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
