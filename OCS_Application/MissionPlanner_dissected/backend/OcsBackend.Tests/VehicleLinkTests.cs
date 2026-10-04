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

    private VehicleLink Start(VehicleLinkConfig config, Func<FakeSerial>? newPort = null) =>
        StartLink(new VehicleLink(config, _ => (newPort ?? (() => NewPort()))()));

    private VehicleLink StartLink(VehicleLink link)
    {
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

    // --- phase 2: battery params and STATUSTEXT -------------------------------------------------

    private static readonly Dictionary<string, float> AllParams = new()
    {
        ["BATT_LOW_VOLT"] = 14.0f, ["BATT_CRT_VOLT"] = 13.2f, ["BATT_LOW_MAH"] = 2000,
        ["BATT_CRT_MAH"] = 1000, ["BATT_CAPACITY"] = 10000,
    };

    private static List<T> Capture<T>(Action<Action<T>> subscribe)
    {
        var list = new List<T>();
        subscribe(x => { lock (list) list.Add(x); });
        return list;
    }

    private static List<T> Snapshot<T>(List<T> list) { lock (list) return list.ToList(); }

    [Fact]
    public void BatteryParamsAreSentOnceOnConnect()
    {
        var link = new VehicleLink(Config(), _ => NewPort());
        var sent = Capture<IReadOnlyDictionary<string, float>>(h => link.ParamsChanged += (_, p) => h(p));
        StartLink(link);

        WaitFor(() => Snapshot(sent).Count == 1, "params");
        Assert.Equal(AllParams, Snapshot(sent)[0]);
        Holds(() => Snapshot(sent).Count == 1, "sent once, not per request round", seconds: 1.5);
    }

    [Fact]
    public void ChangedBatteryParamIsSentAgain()
    {
        var link = new VehicleLink(Config(), _ => NewPort());
        var sent = Capture<IReadOnlyDictionary<string, float>>(h => link.ParamsChanged += (_, p) => h(p));
        StartLink(link);
        WaitFor(() => Snapshot(sent).Count == 1, "params");

        _vehicle.SetParam("BATT_CAPACITY", 10000);  // same value: nothing
        _vehicle.SetParam("BATT_LOW_VOLT", 14.4f);  // e.g. set from another GCS

        WaitFor(() => Snapshot(sent).Count == 2, "params after the change", seconds: 2);
        Assert.Equal(14.4f, Snapshot(sent)[1]["BATT_LOW_VOLT"]);
        Holds(() => Snapshot(sent).Count == 2, "no event for an unchanged value", seconds: 0.5);
    }

    [Fact]
    public void MissingBatteryParamIsReportedWithoutItAfterRetries()
    {
        lock (_vehicle.Params)
            _vehicle.Params.Remove("BATT_CRT_MAH");
        var link = new VehicleLink(Config(), _ => NewPort());
        var sent = Capture<IReadOnlyDictionary<string, float>>(h => link.ParamsChanged += (_, p) => h(p));
        StartLink(link);

        WaitFor(() => Snapshot(sent).Count == 1, "partial params", seconds: 15);
        Assert.Equal(4, Snapshot(sent)[0].Count);
        Assert.False(Snapshot(sent)[0].ContainsKey("BATT_CRT_MAH"));
        lock (_vehicle.Params)
            Assert.Equal(5, _vehicle.ParamRequests.Count(n => n == "BATT_CRT_MAH"));  // maxTries
    }

    [Fact]
    public void StatusTextIsForwardedWithItsSeverity()
    {
        var link = new VehicleLink(Config(), _ => NewPort());
        var texts = Capture<StatusText>(h => link.StatusTextReceived += (_, st) => h(st));
        StartLink(link);
        WaitFor(() => link.State == LinkState.Live, "LIVE");

        var before = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        _vehicle.SendStatusText(MAVLink.MAV_SEVERITY.INFO, "from a companion", compid: 191);  // not the autopilot
        _vehicle.SendStatusText(MAVLink.MAV_SEVERITY.CRITICAL, "PreArm: Battery below minimum");
        _vehicle.SendStatusText(MAVLink.MAV_SEVERITY.DEBUG, "debug too");

        WaitFor(() => Snapshot(texts).Count >= 2, "two status texts", seconds: 2);
        Holds(() => Snapshot(texts).Count == 2, "companion text not forwarded", seconds: 0.3);
        var got = Snapshot(texts);
        Assert.Equal((byte)MAVLink.MAV_SEVERITY.CRITICAL, got[0].Severity);
        Assert.Equal("PreArm: Battery below minimum", got[0].Text);
        Assert.Equal((byte)MAVLink.MAV_SEVERITY.DEBUG, got[1].Severity);
        Assert.InRange(got[0].T, before, before + 2000);
    }

    public void Dispose()
    {
        _stop.Cancel();
        _thread?.Join(TimeSpan.FromSeconds(10));
        _vehicle.Dispose();
    }
}
