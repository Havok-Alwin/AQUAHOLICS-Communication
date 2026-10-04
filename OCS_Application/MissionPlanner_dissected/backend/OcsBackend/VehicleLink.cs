// Logic 8: connect / link-lost, one vehicle per serial link (telemetry radio pair).
// Ported from MP's MainV2.doConnect + MAVLinkInterface.Open (connect) and MainV2.SerialReader
// (read loop, GCS heartbeat, "No Data" warning), without the WinForms parts.
//
// MP (decompiled):
// - Open(getparams:false, showui:false): opens the port, waits up to CONNECT_TIMEOUT_SECONDS
//   (30 s) for 2 heartbeats from one sysid/compid with compid 1 (or 4 from any), skipping GCS
//   heartbeats, then selects that vehicle (sysidcurrent/compidcurrent). With showui:false the
//   NoUIReporter swallows every exception: failure only shows as BaseStream.IsOpen == false.
// - SerialReader: reads while BytesToRead > 10 (at most 1 s per pass), then UpdateCurrentSettings
//   for every MAV; sends a GCS HEARTBEAT (type GCS, autopilot INVALID) once a second; decays
//   linkqualitygcs x0.8 per second once no valid packet for >= 1 s; "WARNING No Data for N
//   Seconds" after 3 s, but only when armed and 30 s after connect.
// - MP never reconnects by itself.
//
// Ours, and why:
// - Link LOST when the vehicle's autopilot sent no valid packet for 1 s (MP's link-quality decay
//   point), armed or not: a frozen display must never pass as a still vehicle. Only the autopilot's
//   own packets count (mav.MAV.lastvalidpacket is per sysid/compid), so a radio that still sends
//   RADIO_STATUS (sysid 51) does not keep a dead vehicle LIVE. Link B sends the status snapshot
//   only while LIVE; the frontend then shows STALE 2 s later (~3 s total, MP's "No Data" time).
// - LOST keeps the port open and keeps reading: a radio dropout heals by itself, as in MP.
//   StreamRates re-requests the streams once data is back.
// - Port gone (closed, or a read throws, e.g. USB unplugged), or connect failed: CLOSED, retry
//   every 5 s with a fresh MAVLinkInterface.
// - Optional expected sysid per vehicle: two radios swapped between USV1 and UAV1 would otherwise
//   show one vehicle's data under the other's name.
// Phase 2: on connect, the battery parameters (BatteryParams, logic 6) and, from then on, every
// STATUSTEXT (logic 7), both raised as events for link B.
using MissionPlanner;
using MissionPlanner.Comms;

namespace Ocs.Backend;

public enum LinkState
{
    /// <summary>No connection. Retrying after RetryDelay.</summary>
    Closed,
    /// <summary>Port open, waiting for the vehicle's heartbeats (MP's Open).</summary>
    Connecting,
    /// <summary>Connected, the autopilot sent a valid packet within LostAfter.</summary>
    Live,
    /// <summary>Connected, but the autopilot has been silent for longer than LostAfter.</summary>
    Lost,
}

/// <summary>One STATUSTEXT, as link B's {ch:'statustext', vehicle, t, severity, text}.</summary>
/// <param name="T">Backend receive time, Unix ms.</param>
/// <param name="Severity">MAV_SEVERITY (0 emergency .. 7 debug).</param>
public sealed record StatusText(long T, byte Severity, string Text);

public sealed record VehicleLinkConfig(string Name, string Port, int Baud = 57600, byte? ExpectedSysId = null)
{
    /// <summary>MP's CONNECT_TIMEOUT_SECONDS default.</summary>
    public TimeSpan ConnectTimeout { get; init; } = TimeSpan.FromSeconds(30);
    public TimeSpan RetryDelay { get; init; } = TimeSpan.FromSeconds(5);
    public TimeSpan LostAfter { get; init; } = TimeSpan.FromSeconds(1);
    /// <summary>GCS heartbeat to the vehicle (MP: once a second).</summary>
    public TimeSpan HeartbeatPeriod { get; init; } = TimeSpan.FromSeconds(1);
}

public sealed class VehicleLink : IDisposable
{
    // MP's SerialReader only calls readPacket while more than this many bytes wait, so a read
    // does not block on a partial packet for ReadTimeout (1.2 s).
    private const int MinBytes = 10;
    private static readonly TimeSpan MaxReadPerPass = TimeSpan.FromSeconds(1);

    private readonly VehicleLinkConfig _config;
    private readonly Func<VehicleLinkConfig, ICommsSerial> _openPort;
    private readonly object _lock = new();

    private MAVLinkInterface? _mav;
    private StreamRates? _rates;
    private BatteryParams? _params;
    private DateTime _retryAt = DateTime.MinValue;
    private DateTime _lastHeartbeatSent = DateTime.MinValue;
    private LinkState _state = LinkState.Closed;

    /// <param name="openPort">Creates the port for a connect attempt (tests pass a fake).</param>
    public VehicleLink(VehicleLinkConfig config, Func<VehicleLinkConfig, ICommsSerial>? openPort = null)
    {
        _config = config;
        _openPort = openPort ?? (c => new SerialPort { PortName = c.Port, BaudRate = c.Baud });
    }

    public string Name => _config.Name;

    public LinkState State { get { lock (_lock) return _state; } }

    /// <summary>Why the last connect failed or the link was dropped; null while connected.</summary>
    public string? Error { get; private set; }

    /// <summary>Raised on every state change, on the link's own thread.</summary>
    public event Action<VehicleLink, LinkState>? StateChanged;

    /// <summary>
    /// Every STATUSTEXT from the vehicle's autopilot, any severity, in arrival order (logic 7).
    /// MP's cs.messages keeps the text without its severity, so the backend hooks the packet.
    /// Raised on the link's own thread. Never drop these downstream: they are a log.
    /// </summary>
    public event Action<VehicleLink, StatusText>? StatusTextReceived;

    /// <summary>Every packet from the vehicle, raised inside readPacket on the link thread (link B
    /// takes ATTITUDE from here). Handlers must be quick and must not throw.</summary>
    public event Action<VehicleLink, MAVLink.MAVLinkMessage>? PacketReceived;

    /// <summary>The five battery parameters (logic 6), on connect and on every change.</summary>
    public event Action<VehicleLink, IReadOnlyDictionary<string, float>>? ParamsChanged;

    /// <summary>
    /// The connected vehicle's MAVLinkInterface (its CurrentState is Mav.MAV.cs), or null.
    /// Its data is current only while State is Live.
    /// </summary>
    public MAVLinkInterface? Mav { get { lock (_lock) return _mav; } }

    /// <summary>Runs the link until cancelled, on the calling thread.</summary>
    public void Run(CancellationToken stop)
    {
        while (!stop.IsCancellationRequested)
        {
            Step();
            Thread.Sleep(State is LinkState.Live or LinkState.Lost ? 1 : 50);  // MP: Task.Delay(1)
        }
        Disconnect();
    }

    /// <summary>One pass of the loop. Blocks while connecting (up to ConnectTimeout).</summary>
    public void Step()
    {
        if (_mav == null)
        {
            if (DateTime.UtcNow >= _retryAt)
                Connect();
            return;
        }

        var mav = _mav;
        if (mav.BaseStream == null || !mav.BaseStream.IsOpen)
        {
            Fail("port closed");
            return;
        }

        try
        {
            var until = DateTime.UtcNow + MaxReadPerPass;
            while (mav.BaseStream.IsOpen && mav.BaseStream.BytesToRead > MinBytes && DateTime.UtcNow < until)
                mav.readPacket();
        }
        catch (Exception e)
        {
            Fail($"read failed: {e.Message}");
            return;
        }

        // MP's per-pass housekeeping: link quality, wind, distance (self-gated to 50 ms).
        // Its built-in stream re-request is off (StreamRates.DisableMpRerequest); ours runs below.
        try { mav.MAV.cs.UpdateCurrentSettings(null, false, mav, mav.MAV); }
        catch (Exception e) { Console.Error.WriteLine($"{Name}: UpdateCurrentSettings failed: {e.Message}"); }

        _rates!.Tick();
        _params!.Tick();
        SendGcsHeartbeat(mav);

        var silent = DateTime.UtcNow - mav.MAV.lastvalidpacket;
        SetState(silent > _config.LostAfter ? LinkState.Lost : LinkState.Live);
    }

    /// <summary>Time since the autopilot's last valid packet, or null if not connected.</summary>
    public TimeSpan? Silence
    {
        get
        {
            var mav = Mav;
            return mav == null ? null : DateTime.UtcNow - mav.MAV.lastvalidpacket;
        }
    }

    private void Connect()
    {
        SetState(LinkState.Connecting);
        var mav = new MAVLinkInterface { CONNECT_TIMEOUT_SECONDS = _config.ConnectTimeout.TotalSeconds };
        try
        {
            mav.BaseStream = _openPort(_config);
            mav.Open(false, false, false);  // getparams, skipconnectedcheck, showui
        }
        catch (Exception e)
        {
            Fail($"connect failed: {e.Message}", mav);
            return;
        }

        // NoUIReporter swallows the reason; MP's doConnect also only checks IsOpen.
        if (mav.BaseStream == null || !mav.BaseStream.IsOpen)
        {
            Fail($"no vehicle on {_config.Port}: port did not open, or no heartbeat within "
                 + $"{_config.ConnectTimeout.TotalSeconds:0} s", mav);
            return;
        }

        var sysid = mav.MAV.sysid;
        if (_config.ExpectedSysId is { } expected && sysid != expected)
        {
            Fail($"wrong vehicle on {_config.Port}: sysid {sysid}, expected {expected} (radios swapped?)", mav);
            return;
        }

        Console.WriteLine($"{Name}: connected on {_config.Port}, sysid {sysid} compid {mav.MAV.compid}, "
                          + $"type {mav.MAV.aptype}, autopilot {mav.MAV.apname}");
        var batt = new BatteryParams(mav, sysid, mav.MAV.compid);
        batt.Changed += p => ParamsChanged?.Invoke(this, p);
        mav.OnPacketReceived += (_, msg) => OnPacket(msg, sysid, mav.MAV.compid, batt);
        lock (_lock)
        {
            _mav = mav;
            _rates = new StreamRates(mav);
            _params = batt;
        }
        _lastHeartbeatSent = DateTime.MinValue;
        Error = null;
        SetState(LinkState.Live);
    }

    // Runs inside mav.readPacket(), on the link thread.
    private void OnPacket(MAVLink.MAVLinkMessage msg, byte sysid, byte compid, BatteryParams batt)
    {
        try
        {
            batt.OnPacket(msg);
            PacketReceived?.Invoke(this, msg);
            // Autopilot only, like MP (its cs.messages are per sysid/compid). One packet, one entry:
            // ArduPilot's texts fit in one packet, so MAVLink 2 chunking (id/chunk_seq) is not joined.
            if (msg.msgid == (uint)MAVLink.MAVLINK_MSG_ID.STATUSTEXT && msg.sysid == sysid && msg.compid == compid)
            {
                var st = msg.ToStructure<MAVLink.mavlink_statustext_t>();
                StatusTextReceived?.Invoke(this, new StatusText(
                    DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), st.severity, BatteryParams.CString(st.text)));
            }
        }
        catch (Exception e)
        {
            Console.Error.WriteLine($"{Name}: packet handler failed: {e.Message}");
        }
    }

    private void SendGcsHeartbeat(MAVLinkInterface mav)
    {
        var now = DateTime.UtcNow;
        if (now - _lastHeartbeatSent < _config.HeartbeatPeriod)
            return;
        _lastHeartbeatSent = now;
        var hb = new MAVLink.mavlink_heartbeat_t
        {
            type = (byte)MAVLink.MAV_TYPE.GCS,
            autopilot = (byte)MAVLink.MAV_AUTOPILOT.INVALID,
            mavlink_version = 3,
        };
        try { mav.sendPacket(hb, mav.MAV.sysid, mav.MAV.compid); }
        catch (Exception e) { Console.Error.WriteLine($"{Name}: GCS heartbeat failed: {e.Message}"); }
    }

    private void Fail(string reason, MAVLinkInterface? pending = null)
    {
        Console.Error.WriteLine($"{Name}: {reason}; retry in {_config.RetryDelay.TotalSeconds:0} s");
        Error = reason;
        pending?.Dispose();
        Disconnect();
        _retryAt = DateTime.UtcNow + _config.RetryDelay;
    }

    private void Disconnect()
    {
        MAVLinkInterface? old;
        lock (_lock)
        {
            old = _mav;
            _mav = null;
            _rates = null;
            _params = null;
        }
        try { old?.Dispose(); } catch { /* closing a dead port */ }
        SetState(LinkState.Closed);
    }

    private void SetState(LinkState next)
    {
        lock (_lock)
        {
            if (_state == next)
                return;
            _state = next;
        }
        StateChanged?.Invoke(this, next);
    }

    public void Dispose() => Disconnect();
}
