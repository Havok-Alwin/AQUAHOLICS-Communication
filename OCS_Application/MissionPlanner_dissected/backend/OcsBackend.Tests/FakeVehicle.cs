// A scripted ArduPilot vehicle behind a FakeSerial: heartbeats and ATTITUDE while the port is open
// and Sending is on, plus optional RADIO_STATUS from the telemetry radio itself. Answers
// PARAM_REQUEST_READ from Params, answers COMMAND_LONG as CommandReply says (arm/disarm and
// DO_SET_MODE change Armed / CustomMode), and can send STATUSTEXT and parameter changes.
namespace Ocs.Backend.Tests;

public sealed class FakeVehicle : IDisposable
{
    private readonly MAVLink.MavlinkParse _gen = new();
    private readonly Timer _timer;
    private readonly byte _sysid;
    private readonly object _sendLock = new();
    private FakeSerial? _port;
    private int _seq, _radioSeq, _tick;

    /// <summary>The port MP currently uses; the test's port factory sets it.</summary>
    public FakeSerial? Port
    {
        get => _port;
        set
        {
            _port = value;
            if (value != null)
                value.OnWrite = OnGcsPacket;
        }
    }

    /// <summary>Autopilot packets on/off (off = vehicle silent, e.g. out of range or powered down).</summary>
    public volatile bool Sending = true;

    /// <summary>The ground radio keeps reporting RADIO_STATUS (sysid 51), as SiK radios do.</summary>
    public volatile bool RadioStatus;

    /// <summary>Parameters the vehicle has. A name not in here is never answered.</summary>
    public Dictionary<string, float> Params { get; } = new()
    {
        ["BATT_LOW_VOLT"] = 14.0f,
        ["BATT_CRT_VOLT"] = 13.2f,
        ["BATT_LOW_MAH"] = 2000,
        ["BATT_CRT_MAH"] = 1000,
        ["BATT_CAPACITY"] = 10000,
    };

    /// <summary>PARAM_REQUEST_READ names received, in order.</summary>
    public List<string> ParamRequests { get; } = new();

    public enum Reply { Accept, Deny, Ignore, InProgressThenAccept }

    /// <summary>How the vehicle answers COMMAND_LONG (default Accept).</summary>
    public volatile Reply CommandReply = Reply.Accept;

    /// <summary>Delay before the final ACCEPTED of InProgressThenAccept.</summary>
    public int InProgressMs = 600;

    /// <summary>COMMAND_LONG received, in order.</summary>
    public List<MAVLink.mavlink_command_long_t> Commands { get; } = new();

    public volatile bool Armed;

    /// <summary>Rover custom mode in the heartbeat (0 Manual, 4 Hold, 10 Auto).</summary>
    public volatile uint CustomMode;

    public FakeVehicle(byte sysid = 1, int periodMs = 50)
    {
        _sysid = sysid;
        _timer = new Timer(_ => Emit(), null, 0, periodMs);
    }

    /// <summary>Sets a parameter and broadcasts PARAM_VALUE, as ArduPilot does on any set.</summary>
    public void SetParam(string name, float value)
    {
        lock (Params)
            Params[name] = value;
        SendParam(name, value);
    }

    public void SendStatusText(MAVLink.MAV_SEVERITY severity, string text, byte compid = 1)
    {
        var bytes = new byte[50];
        System.Text.Encoding.UTF8.GetBytes(text, bytes);
        Send(MAVLink.MAVLINK_MSG_ID.STATUSTEXT,
             new MAVLink.mavlink_statustext_t { severity = (byte)severity, text = bytes }, compid);
    }

    private void OnGcsPacket(byte[] bytes)
    {
        if (!Sending)
            return;
        MAVLink.MAVLinkMessage? msg;
        try { msg = new MAVLink.MavlinkParse().ReadPacket(new MemoryStream(bytes)); }
        catch { return; }
        if (msg != null && msg.msgid == (uint)MAVLink.MAVLINK_MSG_ID.COMMAND_LONG)
        {
            OnCommand(msg.ToStructure<MAVLink.mavlink_command_long_t>());
            return;
        }
        if (msg == null || msg.msgid != (uint)MAVLink.MAVLINK_MSG_ID.PARAM_REQUEST_READ)
            return;
        var req = msg.ToStructure<MAVLink.mavlink_param_request_read_t>();
        var name = System.Text.Encoding.UTF8.GetString(req.param_id).TrimEnd('\0');
        float value;
        lock (Params)
        {
            ParamRequests.Add(name);
            if (!Params.TryGetValue(name, out value))
                return;
        }
        SendParam(name, value);
    }

    private void OnCommand(MAVLink.mavlink_command_long_t cmd)
    {
        lock (Commands)
            Commands.Add(cmd);
        void Ack(MAVLink.MAV_RESULT r) =>
            Send(MAVLink.MAVLINK_MSG_ID.COMMAND_ACK, new MAVLink.mavlink_command_ack_t
            {
                command = cmd.command, result = (byte)r, target_system = 255, target_component = 190,
            });
        void Apply()
        {
            if (cmd.command == (ushort)MAVLink.MAV_CMD.COMPONENT_ARM_DISARM)
                Armed = cmd.param1 == 1;
            else if (cmd.command == (ushort)MAVLink.MAV_CMD.DO_SET_MODE)
                CustomMode = (uint)cmd.param2;
        }
        switch (CommandReply)
        {
            case Reply.Accept:
                Apply();
                Ack(MAVLink.MAV_RESULT.ACCEPTED);
                break;
            case Reply.Deny:
                Ack(MAVLink.MAV_RESULT.DENIED);
                break;
            case Reply.InProgressThenAccept:
                Ack(MAVLink.MAV_RESULT.IN_PROGRESS);
                Task.Delay(InProgressMs).ContinueWith(_ => { Apply(); Ack(MAVLink.MAV_RESULT.ACCEPTED); });
                break;
        }
    }

    private void SendParam(string name, float value)
    {
        var id = new byte[16];
        System.Text.Encoding.UTF8.GetBytes(name, id);
        Send(MAVLink.MAVLINK_MSG_ID.PARAM_VALUE, new MAVLink.mavlink_param_value_t
        {
            param_id = id,
            param_value = value,
            param_type = (byte)MAVLink.MAV_PARAM_TYPE.REAL32,
            param_count = 1000,
            param_index = 0,
        });
    }

    private void Send(MAVLink.MAVLINK_MSG_ID id, object payload, byte compid = 1)
    {
        var port = Port;
        if (port == null || !port.IsOpen)
            return;
        lock (_sendLock)
            port.Feed(_gen.GenerateMAVLinkPacket20(id, payload, false, _sysid, compid, _seq++));
    }

    private void Emit()
    {
        var tick = Interlocked.Increment(ref _tick);
        if (Sending)
        {
            if (tick % 2 == 0)  // heartbeat every other period
            {
                Send(MAVLink.MAVLINK_MSG_ID.HEARTBEAT, new MAVLink.mavlink_heartbeat_t
                {
                    type = (byte)MAVLink.MAV_TYPE.SURFACE_BOAT,
                    autopilot = (byte)MAVLink.MAV_AUTOPILOT.ARDUPILOTMEGA,
                    base_mode = (byte)(MAVLink.MAV_MODE_FLAG.CUSTOM_MODE_ENABLED | (Armed ? MAVLink.MAV_MODE_FLAG.SAFETY_ARMED : 0)),
                    custom_mode = CustomMode,
                    mavlink_version = 3,
                });
            }
            Send(MAVLink.MAVLINK_MSG_ID.ATTITUDE, new MAVLink.mavlink_attitude_t
            {
                time_boot_ms = (uint)(tick * 50), roll = 0.1f, pitch = -0.05f, yaw = 1.0f,
            });
        }
        var port = Port;
        if (RadioStatus && port != null && port.IsOpen)
        {
            var rs = new MAVLink.mavlink_radio_status_t { rssi = 200, remrssi = 190 };
            lock (_sendLock)
                port.Feed(_gen.GenerateMAVLinkPacket20(MAVLink.MAVLINK_MSG_ID.RADIO_STATUS, rs, false, 51, 68, _radioSeq++));
        }
    }

    public void Dispose() => _timer.Dispose();
}
