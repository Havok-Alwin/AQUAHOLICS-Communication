// Logic 3: stream-rate requests, checked on the bytes MP writes to a fake serial port.
using MissionPlanner;
using DS = MAVLink.MAV_DATA_STREAM;

namespace Ocs.Backend.Tests;

public class StreamRatesTests
{
    private const byte SysId = 1, CompId = 1;

    // Marker message per group, as MP's requestDatastream checks it.
    private const uint SysStatus = 1, RawImu = 27, Attitude = 30, GlobalPositionInt = 33,
                       VfrHud = 74, Ahrs = 163;

    private static (MAVLinkInterface mav, FakeSerial port) Connected(byte sysid = SysId)
    {
        StreamRates.DisableMpRerequest();
        var port = new FakeSerial();
        var mav = new MAVLinkInterface { BaseStream = port };
        // What MP's Open() does after the first heartbeat.
        mav.sysidcurrent = sysid;
        mav.compidcurrent = CompId;
        return (mav, port);
    }

    // Pretends the vehicle sends this message at `hz` (fresh), or stopped 10 s ago (stale).
    private static void Arriving(MAVLinkInterface mav, uint msgid, double hz, bool fresh = true)
    {
        mav.MAV.packetspersecond[msgid] = hz;
        mav.MAV.packetspersecondbuild[msgid] = fresh ? DateTime.UtcNow : DateTime.UtcNow.AddSeconds(-10);
    }

    // Every group arriving at its start rate (rate-0 groups simply absent).
    private static void AllAtStartRates(MAVLinkInterface mav)
    {
        Arriving(mav, Attitude, 20);
        Arriving(mav, SysStatus, 2);
        Arriving(mav, GlobalPositionInt, 2);
        Arriving(mav, VfrHud, 2);
        Arriving(mav, Ahrs, 2);
    }

    // (stream, rate) of every REQUEST_DATA_STREAM written, after checking it is well formed.
    private static List<(DS stream, int hz)> Requests(FakeSerial port)
    {
        var result = new List<(DS, int)>();
        foreach (var msg in port.Packets())
        {
            Assert.Equal(66u, msg.msgid);
            var req = msg.ToStructure<MAVLink.mavlink_request_data_stream_t>();
            Assert.Equal(SysId, req.target_system);
            Assert.Equal(CompId, req.target_component);
            Assert.Equal(1, req.start_stop);
            result.Add(((DS)req.req_stream_id, req.req_message_rate));
        }
        return result;
    }

    // MP sends every request twice.
    private static void AssertRequested(FakeSerial port, params (DS stream, int hz)[] expected)
    {
        var twice = expected.SelectMany(e => new[] { e, e }).OrderBy(e => e.stream);
        Assert.Equal(twice, Requests(port).OrderBy(r => r.stream));
    }

    [Fact]
    public void MpBuiltInRerequestIsOff()
    {
        var (mav, port) = Connected();
        Assert.True(StreamRates.MpRerequestDisabled(mav.MAV.cs));

        // MP's first UpdateCurrentSettings call would otherwise request every stream at once.
        mav.MAV.cs.UpdateCurrentSettings(null, true, mav, mav.MAV);

        Assert.Empty(port.Packets());
    }

    [Fact]
    public void NothingBeforeHeartbeat()
    {
        var (mav, port) = Connected(sysid: 0);
        new StreamRates(mav).Tick();
        Assert.Empty(port.Packets());
    }

    [Fact]
    public void NothingWhilePortClosed()
    {
        var (mav, port) = Connected();
        port.Unplug();
        new StreamRates(mav).Tick();
        Assert.Empty(port.Packets());
    }

    [Fact]
    public void FirstTickRequestsEachGroupAtItsOwnRate()
    {
        var (mav, port) = Connected();
        var rates = new StreamRates(mav);

        rates.Tick();

        // EXTRA2 at 2 Hz, not at the attitude rate as MP's re-request would send it.
        // RAW_SENSORS and RC_CHANNELS (0) are not sent: the vehicle is not sending them.
        AssertRequested(port,
            (DS.EXTRA1, 20), (DS.EXTENDED_STATUS, 2), (DS.POSITION, 2), (DS.EXTRA2, 2), (DS.EXTRA3, 2));
        Assert.Equal(1, rates.Rounds);
    }

    [Fact]
    public void GroupArrivingAtRateZeroIsStopped()
    {
        var (mav, port) = Connected();
        AllAtStartRates(mav);
        Arriving(mav, RawImu, 4);

        new StreamRates(mav).Tick();

        AssertRequested(port, (DS.RAW_SENSORS, 0));
    }

    [Fact]
    public void GroupsAlreadyAtRateAreSkipped()
    {
        var (mav, port) = Connected();
        AllAtStartRates(mav);
        Arriving(mav, Attitude, 4);  // the vehicle's default, not ours

        new StreamRates(mav).Tick();

        AssertRequested(port, (DS.EXTRA1, 20));
    }

    [Fact]
    public void ReRequestAfter8sOffRateThenHolds30s()
    {
        var (mav, port) = Connected();
        var t0 = DateTime.UtcNow;
        var now = t0;
        var rates = new StreamRates(mav, utcNow: () => now);

        rates.Tick();
        Assert.Equal(1, rates.Rounds);
        port.Clear();

        // All at rate: nothing, however long it lasts.
        AllAtStartRates(mav);
        now = t0.AddSeconds(100);
        rates.Tick();
        Assert.Empty(port.Packets());

        // Attitude stops. Not re-requested before it has been off for 8 s.
        AllAtStartRates(mav);
        Arriving(mav, Attitude, 20, fresh: false);
        rates.Tick();
        now = t0.AddSeconds(107.9);
        rates.Tick();
        Assert.Empty(port.Packets());

        now = t0.AddSeconds(108);
        rates.Tick();
        AssertRequested(port, (DS.EXTRA1, 20));
        Assert.Equal(2, rates.Rounds);
        port.Clear();

        // Still off: held for 30 s after the last request. (The backend ticks continuously.)
        now = t0.AddSeconds(108.05);
        rates.Tick();
        now = t0.AddSeconds(137.9);
        rates.Tick();
        Assert.Empty(port.Packets());

        now = t0.AddSeconds(138);
        rates.Tick();
        AssertRequested(port, (DS.EXTRA1, 20));
        Assert.Equal(3, rates.Rounds);
    }

    [Fact]
    public void BackAtRateResetsThe8sDelay()
    {
        var (mav, port) = Connected();
        var t0 = DateTime.UtcNow;
        var now = t0;
        var rates = new StreamRates(mav, utcNow: () => now);
        rates.Tick();
        port.Clear();

        // Off at t0+100, back at t0+105, off again at t0+106.
        now = t0.AddSeconds(100);
        AllAtStartRates(mav);
        Arriving(mav, VfrHud, 2, fresh: false);
        rates.Tick();

        now = t0.AddSeconds(105);
        AllAtStartRates(mav);
        rates.Tick();

        now = t0.AddSeconds(106);
        Arriving(mav, VfrHud, 2, fresh: false);
        rates.Tick();

        now = t0.AddSeconds(113.9);
        rates.Tick();
        Assert.Empty(port.Packets());

        now = t0.AddSeconds(114);
        rates.Tick();
        AssertRequested(port, (DS.EXTRA2, 2));
    }
}
