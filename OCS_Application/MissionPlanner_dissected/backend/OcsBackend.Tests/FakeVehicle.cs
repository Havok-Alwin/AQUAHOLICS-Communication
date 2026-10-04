// A scripted ArduPilot vehicle behind a FakeSerial: heartbeats and ATTITUDE while the port is open
// and Sending is on, plus optional RADIO_STATUS from the telemetry radio itself.
namespace Ocs.Backend.Tests;

public sealed class FakeVehicle : IDisposable
{
    private readonly MAVLink.MavlinkParse _gen = new();
    private readonly Timer _timer;
    private readonly byte _sysid;
    private int _seq, _radioSeq, _tick;

    /// <summary>The port MP currently uses; the test's port factory sets it.</summary>
    public FakeSerial? Port { get; set; }

    /// <summary>Autopilot packets on/off (off = vehicle silent, e.g. out of range or powered down).</summary>
    public volatile bool Sending = true;

    /// <summary>The ground radio keeps reporting RADIO_STATUS (sysid 51), as SiK radios do.</summary>
    public volatile bool RadioStatus;

    public FakeVehicle(byte sysid = 1, int periodMs = 50)
    {
        _sysid = sysid;
        _timer = new Timer(_ => Emit(), null, 0, periodMs);
    }

    private void Emit()
    {
        var port = Port;
        if (port == null || !port.IsOpen)
            return;
        var tick = Interlocked.Increment(ref _tick);

        if (Sending)
        {
            if (tick % 2 == 0)  // heartbeat every other period
            {
                var hb = new MAVLink.mavlink_heartbeat_t
                {
                    type = (byte)MAVLink.MAV_TYPE.SURFACE_BOAT,
                    autopilot = (byte)MAVLink.MAV_AUTOPILOT.ARDUPILOTMEGA,
                    mavlink_version = 3,
                };
                port.Feed(_gen.GenerateMAVLinkPacket20(MAVLink.MAVLINK_MSG_ID.HEARTBEAT, hb, false, _sysid, 1, _seq++));
            }
            var att = new MAVLink.mavlink_attitude_t { time_boot_ms = (uint)(tick * 50), roll = 0.1f, pitch = -0.05f, yaw = 1.0f };
            port.Feed(_gen.GenerateMAVLinkPacket20(MAVLink.MAVLINK_MSG_ID.ATTITUDE, att, false, _sysid, 1, _seq++));
        }
        if (RadioStatus)
        {
            var rs = new MAVLink.mavlink_radio_status_t { rssi = 200, remrssi = 190 };
            port.Feed(_gen.GenerateMAVLinkPacket20(MAVLink.MAVLINK_MSG_ID.RADIO_STATUS, rs, false, 51, 68, _radioSeq++));
        }
    }

    public void Dispose() => _timer.Dispose();
}
