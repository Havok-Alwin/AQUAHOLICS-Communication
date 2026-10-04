// Logic 8: connect / link-lost, against a scripted vehicle on a fake serial port.
// These run in real time: MP's Open() waits on real heartbeats and then ~2 s for AUTOPILOT_VERSION.
namespace Ocs.Backend.Tests;

public sealed class VehicleLinkTests : IDisposable
{
    private readonly FakeVehicle _vehicle;
    private readonly List<FakeSerial> _ports = new();
    private readonly CancellationTokenSource _stop = new();
    private Thread? _thread;

    public VehicleLinkTests()
    {
        StreamRates.DisableMpRerequest();
        _vehicle = new FakeVehicle(sysid: 1);
    }

    private static VehicleLinkConfig Config(byte? expectedSysId = 1) => new("USV1", "fake", 57600, expectedSysId)
    {
        ConnectTimeout = TimeSpan.FromSeconds(3),
        RetryDelay = TimeSpan.FromMilliseconds(200),
        LostAfter = TimeSpan.FromMilliseconds(300),
    };

    // Each connect attempt gets a new (closed) port, wired to the vehicle.
    private FakeSerial NewPort(bool failOpen = false)
    {
        var port = new FakeSerial(open: false) { FailOpen = failOpen };
        lock (_ports)
            _ports.Add(port);
        _vehicle.Port = port;
        return port;
    }

    private int PortCount { get { lock (_ports) return _ports.Count; } }

    private VehicleLink Start(VehicleLinkConfig config, Func<FakeSerial>? newPort = null)
    {
        var link = new VehicleLink(config, _ => (newPort ?? (() => NewPort()))());
        _thread = new Thread(() => link.Run(_stop.Token)) { IsBackground = true };
        _thread.Start();
        return link;
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

    // Holds for the whole period (used to check that something does NOT happen).
    private static void Holds(Func<bool> condition, string what, double seconds)
    {
        var until = DateTime.UtcNow.AddSeconds(seconds);
        while (DateTime.UtcNow < until)
        {
            Assert.True(condition(), what);
            Thread.Sleep(10);
        }
    }

    [Fact]
    public void ConnectsThenRequestsStreamsAndSendsGcsHeartbeat()
    {
        var link = Start(Config());

        WaitFor(() => link.State == LinkState.Live, "LIVE");
        Assert.Null(link.Error);
        Assert.Equal(1, link.Mav!.MAV.sysid);

        var port = _ports[0];
        WaitFor(() => port.Packets().Any(p => p.msgid == 66), "REQUEST_DATA_STREAM (logic 3)");
        WaitFor(() => port.Packets().Any(p => p.msgid == 0
            && p.ToStructure<MAVLink.mavlink_heartbeat_t>().type == (byte)MAVLink.MAV_TYPE.GCS), "GCS heartbeat");
    }

    [Fact]
    public void SilentVehicleIsLostThenLiveAgainOnTheSameConnection()
    {
        var link = Start(Config());
        WaitFor(() => link.State == LinkState.Live, "LIVE");
        var mav = link.Mav;

        _vehicle.Sending = false;
        WaitFor(() => link.State == LinkState.Lost, "LOST", seconds: 2);
        Assert.Same(mav, link.Mav);  // port kept open, nothing torn down

        _vehicle.Sending = true;
        WaitFor(() => link.State == LinkState.Live, "LIVE again", seconds: 2);
        Assert.Same(mav, link.Mav);
        Assert.Equal(1, PortCount);  // no reconnect
    }

    [Fact]
    public void RadioStatusAloneDoesNotKeepADeadVehicleLive()
    {
        var link = Start(Config());
        WaitFor(() => link.State == LinkState.Live, "LIVE");

        _vehicle.RadioStatus = true;
        _vehicle.Sending = false;

        WaitFor(() => link.State == LinkState.Lost, "LOST", seconds: 2);
        Holds(() => link.State == LinkState.Lost, "stays LOST while only the radio talks", seconds: 1);
    }

    [Fact]
    public void UnpluggedPortClosesThenReconnects()
    {
        var link = Start(Config());
        WaitFor(() => link.State == LinkState.Live, "LIVE");

        _ports[0].Unplug();

        WaitFor(() => link.State == LinkState.Closed, "CLOSED", seconds: 2);
        Assert.Equal("port closed", link.Error);
        WaitFor(() => link.State == LinkState.Live && PortCount == 2, "LIVE on a new port");
        Assert.Null(link.Error);
    }

    [Fact]
    public void NoHeartbeatFailsAndRetries()
    {
        _vehicle.Sending = false;
        var link = Start(Config());

        WaitFor(() => link.Error != null, "connect failure", seconds: 8);
        Assert.Contains("no heartbeat within 3 s", link.Error);
        Assert.Null(link.Mav);
        WaitFor(() => PortCount >= 2, "a retry", seconds: 8);
    }

    [Fact]
    public void MissingDeviceFailsAndRetries()
    {
        var link = Start(Config(), () => NewPort(failOpen: true));

        WaitFor(() => link.Error != null, "connect failure");
        Assert.Contains("port did not open", link.Error);
        WaitFor(() => PortCount >= 2, "a retry");
    }

    [Fact]
    public void WrongSysIdIsRefused()
    {
        var link = Start(Config(expectedSysId: 2));

        WaitFor(() => link.Error != null, "refusal");
        Assert.Equal("wrong vehicle on fake: sysid 1, expected 2 (radios swapped?)", link.Error);
        Holds(() => link.State != LinkState.Live, "never LIVE", seconds: 0.5);
    }

    public void Dispose()
    {
        _stop.Cancel();
        _thread?.Join(TimeSpan.FromSeconds(10));
        _vehicle.Dispose();
    }
}
