// Logic 3: stream-rate setup.
// Each MAVLink stream group is requested at its own rate (logic 0 needs a fast attitude channel),
// instead of MP's ALL @ 4 Hz. The request itself goes through the MP DLL
// (MAVLinkInterface.requestDatastream: REQUEST_DATA_STREAM, msg 66, sent twice, skipped if the
// group's marker message already arrives at the rate). The re-request schedule is our own.
//
// Why our own schedule (from the decompiled CurrentState.UpdateCurrentSettings):
// - MP re-requests every group from cs.rate*, with EXTRA2 at the *attitude* rate. With EXTRA1 at
//   20 Hz that would push VFR_HUD to 20 Hz and waste radio bandwidth.
// - MP's `lastdata` is only ever set after a re-request (lastdata = now + 30 s), never on
//   received data. So MP does not re-request "after 8 s without data": it re-requests on the
//   first call and then every 38 s, whatever the data does.
// MP's built-in re-request is switched off by setting the cs.rate* defaults to -1, which
// requestDatastream and CameraProtocol.RequestMessageIntervals both treat as "skip".
using MissionPlanner;

namespace Ocs.Backend;

public sealed class StreamRates
{
    // Start rates, decided 2026-10-04. Tune on the bench once the radio air data rate is known.
    // 0 asks the vehicle to stop the group. Groups not listed are left as the vehicle sends them.
    public static readonly IReadOnlyDictionary<MAVLink.MAV_DATA_STREAM, int> StartRates =
        new Dictionary<MAVLink.MAV_DATA_STREAM, int>
        {
            [MAVLink.MAV_DATA_STREAM.EXTRA1] = 20,          // ATTITUDE: the fast channel (logic 0)
            [MAVLink.MAV_DATA_STREAM.EXTENDED_STATUS] = 2,  // SYS_STATUS, GPS_RAW_INT, NAV_CONTROLLER_OUTPUT
            [MAVLink.MAV_DATA_STREAM.POSITION] = 2,         // GLOBAL_POSITION_INT
            [MAVLink.MAV_DATA_STREAM.EXTRA2] = 2,           // VFR_HUD
            [MAVLink.MAV_DATA_STREAM.EXTRA3] = 2,           // AHRS, BATTERY_STATUS, VIBRATION, EKF_STATUS_REPORT
            [MAVLink.MAV_DATA_STREAM.RAW_SENSORS] = 0,      // RAW_IMU, SCALED_PRESSURE
            [MAVLink.MAV_DATA_STREAM.RC_CHANNELS] = 0,      // RC_CHANNELS, SERVO_OUTPUT_RAW
        };

    // The message MP's requestDatastream checks per group (MAVLinkInterface.requestDatastream).
    private static readonly Dictionary<MAVLink.MAV_DATA_STREAM, uint> Marker = new()
    {
        [MAVLink.MAV_DATA_STREAM.EXTENDED_STATUS] = 1,  // SYS_STATUS
        [MAVLink.MAV_DATA_STREAM.EXTRA1] = 30,          // ATTITUDE
        [MAVLink.MAV_DATA_STREAM.EXTRA2] = 74,          // VFR_HUD
        [MAVLink.MAV_DATA_STREAM.EXTRA3] = 163,         // AHRS
        [MAVLink.MAV_DATA_STREAM.POSITION] = 33,        // GLOBAL_POSITION_INT
        [MAVLink.MAV_DATA_STREAM.RAW_CONTROLLER] = 34,  // RC_CHANNELS_SCALED
        [MAVLink.MAV_DATA_STREAM.RAW_SENSORS] = 27,     // RAW_IMU
        [MAVLink.MAV_DATA_STREAM.RC_CHANNELS] = 35,     // RC_CHANNELS_RAW
    };

    // MP only trusts a group's measured rate if its marker arrived in the last 2 s.
    private static readonly TimeSpan MarkerFresh = TimeSpan.FromSeconds(2);

    /// <summary>
    /// Switches off MP's built-in re-request in CurrentState.UpdateCurrentSettings.
    /// Call before any MAVLinkInterface is created: every CurrentState copies the static
    /// cs.rate*backup values in its constructor (ResetInternals), so all vehicles get -1.
    /// </summary>
    public static void DisableMpRerequest()
    {
        CurrentState.rateattitudebackup = -1;
        CurrentState.ratepositionbackup = -1;
        CurrentState.ratestatusbackup = -1;
        CurrentState.ratesensorsbackup = -1;
        CurrentState.ratercbackup = -1;
    }

    /// <summary>True if this CurrentState will not re-request streams by itself.</summary>
    public static bool MpRerequestDisabled(CurrentState cs) =>
        cs.rateattitude == -1 && cs.rateposition == -1 && cs.ratestatus == -1
        && cs.ratesensors == -1 && cs.raterc == -1;

    private readonly MAVLinkInterface _mav;
    private readonly IReadOnlyDictionary<MAVLink.MAV_DATA_STREAM, int> _rates;
    private readonly TimeSpan _offDelay;
    private readonly TimeSpan _hold;
    private readonly Func<DateTime> _utcNow;

    private bool _requested;
    private DateTime? _offSince;
    private DateTime _holdUntil;

    /// <param name="offDelay">How long a group must be off its rate before a re-request (8 s).</param>
    /// <param name="hold">Minimum time between two requests (30 s, MP's wait after a re-request).</param>
    /// <param name="utcNow">Clock, for tests. Must be UTC: MP stamps markers with DateTime.UtcNow.</param>
    public StreamRates(MAVLinkInterface mav,
                       IReadOnlyDictionary<MAVLink.MAV_DATA_STREAM, int>? rates = null,
                       TimeSpan? offDelay = null, TimeSpan? hold = null,
                       Func<DateTime>? utcNow = null)
    {
        _mav = mav;
        _rates = rates ?? StartRates;
        _offDelay = offDelay ?? TimeSpan.FromSeconds(8);
        _hold = hold ?? TimeSpan.FromSeconds(30);
        _utcNow = utcNow ?? (() => DateTime.UtcNow);
    }

    /// <summary>Number of request rounds sent so far.</summary>
    public int Rounds { get; private set; }

    /// <summary>
    /// Call regularly from the backend loop, next to cs.UpdateCurrentSettings.
    /// First call with an open port and a known vehicle: request every group.
    /// Afterwards: re-request once any group has been off its rate for offDelay, at most every hold.
    /// </summary>
    public void Tick()
    {
        if (_mav.BaseStream == null || !_mav.BaseStream.IsOpen)
            return;
        var mavState = _mav.MAV;
        if (mavState.sysid == 0)  // no heartbeat yet: no vehicle to address
            return;

        var now = _utcNow();
        try
        {
            if (!_requested)
            {
                RequestAll(mavState, now);
                return;
            }

            if (AllAtRate(mavState))
            {
                _offSince = null;
                return;
            }
            _offSince ??= now;
            if (now - _offSince >= _offDelay && now >= _holdUntil)
                RequestAll(mavState, now);
        }
        catch (Exception e)
        {
            // MP reads its packet-rate tables from this thread while the reader thread writes them
            // (as MP itself does). A torn read only delays the check to the next tick.
            Console.Error.WriteLine($"StreamRates: rate check failed: {e.Message}");
        }
    }

    private void RequestAll(MAVState mavState, DateTime now)
    {
        // MP skips each group that already arrives at its rate, so only off-rate groups are sent.
        foreach (var (stream, hz) in _rates)
            _mav.requestDatastream(stream, hz, mavState.sysid, mavState.compid);
        _requested = true;
        _offSince = null;
        _holdUntil = now + _hold;
        Rounds++;
    }

    // Same test as MP's requestDatastream + hzratecheck, so "off" here means MP would send.
    private bool AllAtRate(MAVState mavState)
    {
        var now = DateTime.UtcNow;  // MP's timestamps, not the injected clock
        foreach (var (stream, hz) in _rates)
        {
            if (hz == -1 || !Marker.TryGetValue(stream, out var msgid))
                continue;
            double pps = 0;
            if (mavState.packetspersecondbuild.TryGetValue(msgid, out var last)
                && last >= now - MarkerFresh)
                pps = mavState.packetspersecond[msgid];
            if (!HzRateCheck(pps, hz))
                return false;
        }
        return true;
    }

    // Port of MAVLinkInterface.hzratecheck (private in MP): at rate if pps is in (hz-1, hz+0.1),
    // or if both are 0.
    private static bool HzRateCheck(double pps, int hz)
    {
        if (double.IsInfinity(pps))
            return false;
        if (hz == 0 && pps == 0)
            return true;
        return pps > hz - 1 && pps < hz + 0.1;
    }
}
