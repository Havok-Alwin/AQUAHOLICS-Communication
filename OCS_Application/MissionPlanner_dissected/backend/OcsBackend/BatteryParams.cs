// Logic 8 phase 2: the five battery parameters the frontend's warnings need (logic 6), fetched on
// connect and re-sent whenever one changes. Becomes the link B message
// {ch:'params', vehicle, params:{BATT_LOW_VOLT, ...}}.
//
// MP reads these from MAV.param, which Open(getparams:false) never fills. MP's GetParam blocks the
// caller for up to 4 x 700 ms per parameter while it reads packets itself. Ours does not block the
// link loop: it sends PARAM_REQUEST_READ for the missing names (GetParam with requireresponce:false)
// and takes the answers from the normal read loop. That also catches changes: ArduPilot broadcasts
// PARAM_VALUE whenever a parameter is set, by any GCS.
using MissionPlanner;

namespace Ocs.Backend;

public sealed class BatteryParams
{
    public static readonly string[] Names =
        { "BATT_LOW_VOLT", "BATT_CRT_VOLT", "BATT_LOW_MAH", "BATT_CRT_MAH", "BATT_CAPACITY" };

    private readonly MAVLinkInterface _mav;
    private readonly byte _sysid, _compid;
    private readonly TimeSpan _retryPeriod;
    private readonly int _maxTries;
    private readonly Func<DateTime> _utcNow;
    private readonly Dictionary<string, float> _values = new();

    private int _tries;
    private DateTime _nextTry = DateTime.MinValue;
    private bool _reported;

    /// <param name="retryPeriod">Time between request rounds for the missing names (1 s).</param>
    /// <param name="maxTries">Request rounds before reporting what arrived (5). A name the
    /// firmware does not have never answers; the frontend shows "limits unknown" for it.</param>
    public BatteryParams(MAVLinkInterface mav, byte sysid, byte compid,
                         TimeSpan? retryPeriod = null, int maxTries = 5, Func<DateTime>? utcNow = null)
    {
        _mav = mav;
        _sysid = sysid;
        _compid = compid;
        _retryPeriod = retryPeriod ?? TimeSpan.FromSeconds(1);
        _maxTries = maxTries;
        _utcNow = utcNow ?? (() => DateTime.UtcNow);
    }

    /// <summary>
    /// The parameter set to send. Raised once all five arrived, or after maxTries with what did,
    /// then again on every change. Raised on the link's own thread.
    /// </summary>
    public event Action<IReadOnlyDictionary<string, float>>? Changed;

    public bool Complete => _values.Count == Names.Length;

    /// <summary>Call from the link loop: requests the missing names, at most every retryPeriod.</summary>
    public void Tick()
    {
        if (Complete)
            return;
        var now = _utcNow();
        if (now < _nextTry)
            return;

        if (_tries >= _maxTries)
        {
            if (!_reported)
                Report();
            return;
        }

        foreach (var name in Names)
            if (!_values.ContainsKey(name))
                _mav.GetParam(_sysid, _compid, name, -1, false);  // send only, no wait
        _tries++;
        _nextTry = now + _retryPeriod;
    }

    /// <summary>Call for every packet received (MAVLinkInterface.OnPacketReceived).</summary>
    public void OnPacket(MAVLink.MAVLinkMessage msg)
    {
        if (msg.msgid != (uint)MAVLink.MAVLINK_MSG_ID.PARAM_VALUE || msg.sysid != _sysid || msg.compid != _compid)
            return;
        var pv = msg.ToStructure<MAVLink.mavlink_param_value_t>();
        var name = CString(pv.param_id);
        if (Array.IndexOf(Names, name) < 0)
            return;

        // ArduPilot sends every parameter as its numeric value in a float, integers included
        // (MP treats ArduPilot params as REAL32 for the same reason).
        if (_values.TryGetValue(name, out var old) && old == pv.param_value)
            return;
        _values[name] = pv.param_value;
        if (_reported || Complete)
            Report();
    }

    private void Report()
    {
        _reported = true;
        Changed?.Invoke(new Dictionary<string, float>(_values));
    }

    internal static string CString(byte[] bytes)
    {
        var end = Array.IndexOf(bytes, (byte)0);
        return System.Text.Encoding.UTF8.GetString(bytes, 0, end < 0 ? bytes.Length : end);
    }
}
