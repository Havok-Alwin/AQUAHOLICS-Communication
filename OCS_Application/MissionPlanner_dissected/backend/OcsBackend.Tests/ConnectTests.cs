// Connect from the display: OFF slots, Configure (connect / disconnect / reconnect), link B
// connect messages, port discovery, and the saved connections.
using System.Text.Json;

namespace Ocs.Backend.Tests;

public sealed class ConnectTests : IDisposable
{
    private readonly FakeVehicle _vehicle = new(sysid: 1);
    private readonly CancellationTokenSource _stop = new();
    private readonly List<FakeSerial> _ports = new();
    private readonly List<string> _opened = new();
    private readonly VehicleLink _link;
    private readonly Thread _thread;

    public ConnectTests()
    {
        StreamRates.DisableMpRerequest();
        _link = new VehicleLink("USV1", null, c =>
        {
            var port = new FakeSerial(open: false);
            lock (_ports)
            {
                _ports.Add(port);
                _opened.Add(c.Port);
            }
            _vehicle.Port = port;
            return port;
        });
        _thread = new Thread(() => _link.Run(_stop.Token)) { IsBackground = true };
        _thread.Start();
    }

    private static VehicleLinkConfig Config(string port, byte? sysid = null) => new("USV1", port, 115200, sysid)
    {
        ConnectTimeout = TimeSpan.FromSeconds(3),
        RetryDelay = TimeSpan.FromMilliseconds(200),
    };

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

    private int Opened { get { lock (_ports) return _ports.Count; } }

    [Fact]
    public void SlotWithoutPortStaysOffAndOpensNothing()
    {
        Thread.Sleep(500);
        Assert.Equal(LinkState.Off, _link.State);
        Assert.Null(_link.Config);
        Assert.Equal(0, Opened);
    }

    [Fact]
    public void ConnectDisconnectReconnect()
    {
        _link.Configure(Config("/dev/fake0"));
        WaitFor(() => _link.State == LinkState.Live, "LIVE after Connect");
        Assert.Equal("/dev/fake0", _link.Config!.Port);

        _link.Configure(null);
        WaitFor(() => _link.State == LinkState.Off, "OFF after Disconnect");
        lock (_ports)
            Assert.False(_ports[0].IsOpen);  // port released
        Assert.Null(_link.Mav);

        _link.Configure(Config("/dev/fake1"));  // reconnect, other port
        WaitFor(() => _link.State == LinkState.Live, "LIVE on the new port");
        lock (_ports)
            Assert.Equal(new[] { "/dev/fake0", "/dev/fake1" }, _opened);
    }

    [Fact]
    public void ReconnectingTheSamePortDropsAndReopensIt()
    {
        _link.Configure(Config("/dev/fake0"));
        WaitFor(() => _link.State == LinkState.Live, "LIVE");
        _link.Configure(_link.Config);  // the display's Reconnect
        WaitFor(() => Opened == 2 && _link.State == LinkState.Live, "LIVE again on a fresh port");
        lock (_ports)
            Assert.False(_ports[0].IsOpen);
    }

    [Fact]
    public void DisconnectCancelsAConnectAttemptAtOnce()
    {
        _vehicle.Sending = false;  // wrong port / no vehicle: MP's Open() would wait the full timeout
        _link.Configure(Config("/dev/fake0") with { ConnectTimeout = TimeSpan.FromSeconds(30) });
        WaitFor(() => _link.State == LinkState.Connecting, "CONNECTING");
        Thread.Sleep(500);

        var start = DateTime.UtcNow;
        _link.Configure(null);
        WaitFor(() => _link.State == LinkState.Off, "OFF", seconds: 6);
        Assert.True(DateTime.UtcNow - start < TimeSpan.FromSeconds(4), $"took {(DateTime.UtcNow - start).TotalSeconds:0.0} s");
        Assert.Null(_link.Error);  // a cancel is not a failure
        lock (_ports)
            Assert.False(_ports[0].IsOpen);
    }

    public void Dispose()
    {
        _stop.Cancel();
        _thread.Join(TimeSpan.FromSeconds(10));
        _vehicle.Dispose();
    }
}

public sealed class LinkBConnectTests : IDisposable
{
    private readonly VehicleLink _usv = new("USV1", null, _ => new FakeSerial(open: false));
    private readonly VehicleLink _uav = new("UAV1", null, _ => new FakeSerial(open: false));
    private readonly string _settingsFile = Path.Combine(Path.GetTempPath(), $"ocs-vehicles-{Guid.NewGuid():N}.json");
    private readonly LinkBHub _hub;
    private readonly List<CommandUpdate> _acks = new();

    public LinkBConnectTests()
    {
        var ports = new[] { new SerialPortInfo("/dev/serial/by-id/usb-ArduPilot_Pixhawk1_X-if00", "ArduPilot Pixhawk1 (ttyACM0)") };
        _hub = new LinkBHub(new[] { _usv, _uav }, settings: new VehicleSettings(_settingsFile), listPorts: () => ports);
        _hub.ConnectReplied += (_, u) => _acks.Add(u);
    }

    private const string Port = "/dev/serial/by-id/usb-ArduPilot_Pixhawk1_X-if00";

    [Fact]
    public void ConnectSetsTheSlotAndIsSaved()
    {
        _hub.OnClientMessage($$"""{"ch":"connect","id":"c1","vehicle":"USV1","port":"{{Port}}","baud":115200,"sysid":1}""");

        Assert.Equal(Port, _usv.Config!.Port);
        Assert.Equal(115200, _usv.Config.Baud);
        Assert.Equal((byte)1, _usv.Config.ExpectedSysId);
        Assert.Equal("accepted", _acks.Single(a => a.Id == "c1").Status);
        var saved = new VehicleSettings(_settingsFile).Load();
        Assert.Equal(Port, saved["USV1"]!.Port);
        Assert.Null(saved["UAV1"]);

        _hub.OnClientMessage("""{"ch":"disconnect","id":"d1","vehicle":"USV1"}""");
        Assert.Null(_usv.Config);
        Assert.Null(new VehicleSettings(_settingsFile).Load()["USV1"]);
    }

    [Fact]
    public void OnlyListedPortsSupportedBaudsAndOneVehiclePerPort()
    {
        _hub.OnClientMessage("""{"ch":"connect","id":"x1","vehicle":"USV1","port":"/etc/passwd","baud":57600}""");
        _hub.OnClientMessage($$"""{"ch":"connect","id":"x2","vehicle":"USV1","port":"{{Port}}","baud":12345}""");
        _hub.OnClientMessage($$"""{"ch":"connect","id":"x3","vehicle":"USV1","port":"{{Port}}","baud":57600,"sysid":300}""");
        Assert.All(_acks, a => Assert.Equal("error", a.Status));
        Assert.Null(_usv.Config);

        _hub.OnClientMessage($$"""{"ch":"connect","id":"ok","vehicle":"USV1","port":"{{Port}}","baud":57600}""");
        _hub.OnClientMessage($$"""{"ch":"connect","id":"dup","vehicle":"UAV1","port":"{{Port}}","baud":57600}""");
        Assert.Contains("already used by USV1", _acks.Single(a => a.Id == "dup").Detail);
        Assert.Null(_uav.Config);
    }

    [Fact]
    public void BackendMessageCarriesPortsAndEachSlotsConnection()
    {
        _usv.Configure(new VehicleLinkConfig("USV1", Port, 57600, 1));
        var json = JsonDocument.Parse(LinkBMessages.Backend(1, new[] { _usv, _uav },
            new[] { new SerialPortInfo(Port, "ArduPilot Pixhawk1 (ttyACM0)") })).RootElement;

        Assert.Equal(Port, json.GetProperty("ports")[0].GetProperty("path").GetString());
        var usv = json.GetProperty("links").GetProperty("USV1");
        Assert.Equal(Port, usv.GetProperty("port").GetString());
        Assert.Equal(1, usv.GetProperty("sysid").GetInt32());
        Assert.Equal("off", json.GetProperty("links").GetProperty("UAV1").GetProperty("state").GetString());
        Assert.False(json.GetProperty("links").GetProperty("UAV1").TryGetProperty("port", out _));
    }

    public void Dispose()
    {
        _hub.Dispose();
        File.Delete(_settingsFile);
    }
}

public sealed class SerialPortsTests : IDisposable
{
    private readonly string _root = Directory.CreateTempSubdirectory("ocs-dev").FullName;

    [Fact]
    public void ByIdNamesFirstThenOtherAcmAndUsbPorts()
    {
        var dev = Directory.CreateDirectory(Path.Combine(_root, "dev")).FullName;
        var byId = Directory.CreateDirectory(Path.Combine(dev, "serial", "by-id")).FullName;
        foreach (var name in new[] { "ttyACM0", "ttyUSB0", "ttyS0", "tty" })
            File.WriteAllText(Path.Combine(dev, name), "");
        File.CreateSymbolicLink(Path.Combine(byId, "usb-ArduPilot_Pixhawk1_25001B000551333532383533-if00"), Path.Combine(dev, "ttyACM0"));

        var ports = SerialPorts.List(byId, dev);

        Assert.Equal(new[] { "ArduPilot Pixhawk1 (ttyACM0)", "ttyUSB0" }, ports.Select(p => p.Label));
        Assert.EndsWith("by-id/usb-ArduPilot_Pixhawk1_25001B000551333532383533-if00", ports[0].Path);
    }

    public void Dispose() => Directory.Delete(_root, recursive: true);
}
