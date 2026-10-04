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
// Logic 9: operator commands (VehicleCommands.cs) are queued by Submit and run on the link thread,
// so the port keeps a single reader and the GCS heartbeat never pauses.
using System.Collections.Concurrent;
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
    /// <summary>No port chosen: the operator has not connected this vehicle (or disconnected it).</summary>
    Off,
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
    /// <summary>Wait for a COMMAND_ACK before a retry (MP doCommand: 2 s; arm/disarm 10 s).</summary>
    public TimeSpan CommandTimeout { get; init; } = TimeSpan.FromSeconds(2);
    public TimeSpan ArmTimeout { get; init; } = TimeSpan.FromSeconds(10);
}

public sealed class VehicleLink : IDisposable
{
    // MP's SerialReader only calls readPacket while more than this many bytes wait, so a read
    // does not block on a partial packet for ReadTimeout (1.2 s).
    private const int MinBytes = 10;
    private static readonly TimeSpan MaxReadPerPass = TimeSpan.FromSeconds(1);

    private VehicleLinkConfig? _config;
    private readonly string _name;
    private readonly Func<VehicleLinkConfig, ICommsSerial> _openPort;
    private readonly object _lock = new();
    private bool _reconfigure;
    private VehicleLinkConfig? _pendingConfig;
    // The MAVLinkInterface inside MP's blocking Open(), so Configure can cancel it.
    private volatile MAVLinkInterface? _opening;

    private MAVLinkInterface? _mav;
    private StreamRates? _rates;
    private BatteryParams? _params;
    private VehicleCommands? _commands;
    private readonly ConcurrentQueue<VehicleCommand> _commandQueue = new();
    private DateTime _retryAt = DateTime.MinValue;
    private DateTime _lastHeartbeatSent = DateTime.MinValue;
    private LinkState _state = LinkState.Closed;

    /// <param name="openPort">Creates the port for a connect attempt (tests pass a fake).</param>
    public VehicleLink(VehicleLinkConfig config, Func<VehicleLinkConfig, ICommsSerial>? openPort = null)
        : this(config.Name, config, openPort)
    {
    }

    /// <summary>A vehicle slot; null config: OFF until Configure (the operator presses Connect).</summary>
    public VehicleLink(string name, VehicleLinkConfig? config, Func<VehicleLinkConfig, ICommsSerial>? openPort = null)
    {
        _name = name;
        _config = config;
        _state = config == null ? LinkState.Off : LinkState.Closed;
        _openPort = openPort ?? (c => new SerialPort { PortName = c.Port, BaudRate = c.Baud });
    }

    public string Name => _name;

    // The active config, on the link thread while connecting or connected (never OFF there).
    private VehicleLinkConfig Cfg => _config ?? throw new InvalidOperationException($"{_name} is not configured");

    /// <summary>The port this slot uses (or tries to use); null while OFF.</summary>
    public VehicleLinkConfig? Config { get { lock (_lock) return _reconfigure ? _pendingConfig : _config; } }

    /// <summary>
    /// Connect this slot to a port (config), or disconnect it (null). Thread-safe; the link thread
    /// drops the current connection and applies it on its next pass. Reconnect = Configure(Config).
    /// </summary>
    public void Configure(VehicleLinkConfig? config)
    {
        if (config != null && config.Name != _name)
            config = config with { Name = _name };
        lock (_lock)
        {
            _pendingConfig = config;
            _reconfigure = true;
        }
        CancelOpen();
    }

    // MP's Open() blocks the link thread for up to CONNECT_TIMEOUT_SECONDS waiting for heartbeats. Its
    // loop checks frmProgressReporter.doWorkArgs.CancelRequested on every pass (each heartbeat wait is
    // at most 2.2 s), so a Disconnect / Reconnect takes effect within seconds, not after the timeout.
    // The reporter exists only once Open() has started, so wait briefly for it.
    private void CancelOpen()
    {
        var mav = _opening;
        if (mav == null)
            return;
        Task.Run(async () =>
        {
            for (var i = 0; i < 100 && _opening == mav; i++)
            {
                if (mav.frmProgressReporter?.doWorkArgs is { } args)
                {
                    args.CancelRequested = true;
                    return;
                }
                await Task.Delay(50);
            }
        });
    }

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

    /// <summary>Progress of every command (logic 9): "sent", then accepted/rejected/timeout/error.
    /// Raised on the link thread, or on the caller's thread for an immediate refusal.</summary>
    public event Action<VehicleLink, CommandUpdate>? CommandUpdated;

    /// <summary>The connected vehicle's flight modes (MP's names for its firmware), on connect.</summary>
    public event Action<VehicleLink, IReadOnlyList<string>>? ModesChanged;

    /// <summary>The flight modes of the connected vehicle; empty while not connected.</summary>
    public IReadOnlyList<string> Modes { get; private set; } = Array.Empty<string>();

    /// <summary>
    /// Queues a command for the link thread. Refused at once if the kind is unknown or the link is
    /// not LIVE. Thread-safe.
    /// </summary>
    public void Submit(VehicleCommand cmd)
    {
        if (Array.IndexOf(VehicleCommands.Kinds, cmd.Kind) < 0)
            CommandUpdated?.Invoke(this, new CommandUpdate(cmd.Id, cmd.Kind, "error", $"unknown command '{cmd.Kind}'"));
        else if (State != LinkState.Live)
            CommandUpdated?.Invoke(this, new CommandUpdate(cmd.Id, cmd.Kind, "error", $"vehicle link is {State.ToString().ToUpperInvariant()}, not LIVE"));
        else
            _commandQueue.Enqueue(cmd);
    }

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
            if (State == LinkState.Off)
                Thread.Sleep(200);
        }
        Disconnect();
    }

    /// <summary>One pass of the loop. Blocks while connecting (up to ConnectTimeout).</summary>
    public void Step()
    {
        bool reconfigure;
        VehicleLinkConfig? next;
        lock (_lock)
        {
            reconfigure = _reconfigure;
            next = _pendingConfig;
            _reconfigure = false;
        }
        if (reconfigure)
        {
            Disconnect();
            _config = next;
            Error = null;
            _retryAt = DateTime.MinValue;
        }
        if (_config == null)
        {
            if (_mav != null)
                Disconnect();
            SetState(LinkState.Off);
            return;
        }

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
        while (_commandQueue.TryDequeue(out var cmd))
        {
            if (State == LinkState.Live)
                _commands!.Start(cmd);
            else
                CommandUpdated?.Invoke(this, new CommandUpdate(cmd.Id, cmd.Kind, "error", $"vehicle link is {State.ToString().ToUpperInvariant()}, not LIVE"));
        }
        _commands!.Tick();
        SendGcsHeartbeat(mav);

        var silent = DateTime.UtcNow - mav.MAV.lastvalidpacket;
        SetState(silent > Cfg.LostAfter ? LinkState.Lost : LinkState.Live);
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
        var mav = new MAVLinkInterface { CONNECT_TIMEOUT_SECONDS = Cfg.ConnectTimeout.TotalSeconds };
        try
        {
            mav.BaseStream = _openPort(Cfg);
            _opening = mav;
            if (_reconfigure)  // a Configure that came before Open() started
                CancelOpen();
            mav.Open(false, false, false);  // getparams, skipconnectedcheck, showui
        }
        catch (Exception e)
        {
            _opening = null;
            Fail($"connect failed: {e.Message}", mav);
            return;
        }
        _opening = null;

        bool reconfigured;
        lock (_lock)
            reconfigured = _reconfigure;
        if (reconfigured)
        {
            // Cancelled by Disconnect / Reconnect: not a failure; the next pass applies the new config.
            try { mav.Dispose(); } catch { /* port already closed by Open's cancel */ }
            SetState(LinkState.Closed);
            return;
        }

        // NoUIReporter swallows the reason; MP's doConnect also only checks IsOpen.
        if (mav.BaseStream == null || !mav.BaseStream.IsOpen)
        {
            Fail($"no vehicle on {Cfg.Port}: port did not open, or no heartbeat within "
                 + $"{Cfg.ConnectTimeout.TotalSeconds:0} s", mav);
            return;
        }

        var sysid = mav.MAV.sysid;
        if (Cfg.ExpectedSysId is { } expected && sysid != expected)
        {
            Fail($"wrong vehicle on {Cfg.Port}: sysid {sysid}, expected {expected} (radios swapped?)", mav);
            return;
        }

        Console.WriteLine($"{Name}: connected on {Cfg.Port}, sysid {sysid} compid {mav.MAV.compid}, "
                          + $"type {mav.MAV.aptype}, autopilot {mav.MAV.apname}");
        var batt = new BatteryParams(mav, sysid, mav.MAV.compid);
        batt.Changed += p => ParamsChanged?.Invoke(this, p);
        var commands = new VehicleCommands(mav, sysid, mav.MAV.compid, u => CommandUpdated?.Invoke(this, u),
                                           Cfg.CommandTimeout, Cfg.ArmTimeout);
        mav.OnPacketReceived += (_, msg) => OnPacket(msg, sysid, mav.MAV.compid, batt, commands);
        lock (_lock)
        {
            _mav = mav;
            _rates = new StreamRates(mav);
            _params = batt;
            _commands = commands;
        }
        Modes = VehicleCommands.ModesOf(mav);
        ModesChanged?.Invoke(this, Modes);
        _lastHeartbeatSent = DateTime.MinValue;
        Error = null;
        SetState(LinkState.Live);
    }

    // Runs inside mav.readPacket(), on the link thread.
    private void OnPacket(MAVLink.MAVLinkMessage msg, byte sysid, byte compid, BatteryParams batt, VehicleCommands commands)
    {
        try
        {
            batt.OnPacket(msg);
            commands.OnPacket(msg);
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
        if (now - _lastHeartbeatSent < Cfg.HeartbeatPeriod)
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
        Console.Error.WriteLine($"{Name}: {reason}; retry in {Cfg.RetryDelay.TotalSeconds:0} s");
        Error = reason;
        pending?.Dispose();
        Disconnect();
        _retryAt = DateTime.UtcNow + Cfg.RetryDelay;
    }

    private void Disconnect()
    {
        MAVLinkInterface? old;
        VehicleCommands? commands;
        lock (_lock)
        {
            old = _mav;
            commands = _commands;
            _mav = null;
            _rates = null;
            _params = null;
            _commands = null;
        }
        commands?.Abort("vehicle link closed");
        while (_commandQueue.TryDequeue(out var cmd))
            CommandUpdated?.Invoke(this, new CommandUpdate(cmd.Id, cmd.Kind, "error", "vehicle link closed"));
        if (old != null)
        {
            Modes = Array.Empty<string>();
            ModesChanged?.Invoke(this, Modes);
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
