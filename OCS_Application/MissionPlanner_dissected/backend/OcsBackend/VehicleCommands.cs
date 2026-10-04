// Logic 9: vehicle commands (arm, disarm, mode change), one vehicle link at a time.
//
// MP (decompiled MAVLinkInterface):
// - doARM -> doCommand(COMPONENT_ARM_DISARM, p1 = 1/0); force uses the magic p2 2989 / 21196.
// - doCommand(requireack): sends COMMAND_LONG, then READS PACKETS ITSELF until the COMMAND_ACK
//   (giveComport pauses MP's reader thread meanwhile). Timeout 2 s, 10 s for arm/disarm; 3 retries
//   with confirmation+1; result IN_PROGRESS (5) restarts the wait and stops the retries;
//   ACCEPTED (0) is success, anything else failure.
// - setMode(name): translateMode looks the name up in Common.getModesList(cs.firmware) (the
//   parameter metadata: FLTMODE1 for Copter, MODE1 for Rover), then sends DO_SET_MODE without
//   waiting for an ack, plus SET_MODE (msg 11) twice.
//
// Ours, and why:
// - Non-blocking: the COMMAND_LONG is sent from the link thread and the COMMAND_ACK arrives through
//   the link's normal read loop. Calling MP's doCommand would make a second reader on the port, or
//   stall the link loop for up to 4 x 10 s without our GCS heartbeat (vehicle GCS failsafe).
//   MP's timeouts, retries, confirmation counter and IN_PROGRESS rule are kept.
// - Mode change waits for the DO_SET_MODE ack too, so the operator sees accepted / rejected
//   (ArduPilot acks DO_SET_MODE). SET_MODE (msg 11) is not sent: DO_SET_MODE covers ArduPilot 4.x.
// - No force arm/disarm: force skips the pre-arm checks, and a forced disarm in flight drops a UAV.
// - One command at a time per vehicle; a second one is refused as busy.
// - Only while the link is LIVE.
using MissionPlanner;

namespace Ocs.Backend;

/// <summary>A command from the operator display. Kind: "arm", "disarm" or "mode" (with Mode).</summary>
public sealed record VehicleCommand(string Id, string Kind, string? Mode = null);

/// <summary>
/// Progress of a command. Status: "sent" (waiting for the vehicle), then one final status:
/// "accepted", "rejected" (the vehicle refused, Detail = MAV_RESULT), "timeout", or "error"
/// (never sent: not live, busy, unknown mode...).
/// </summary>
public sealed record CommandUpdate(string Id, string Kind, string Status, string Detail);

internal sealed class VehicleCommands
{
    public static readonly string[] Kinds = { "arm", "disarm", "mode" };

    private readonly MAVLinkInterface _mav;
    private readonly byte _sysid, _compid;
    private readonly Action<CommandUpdate> _update;
    private readonly TimeSpan _timeout, _armTimeout;
    private readonly int _retries;
    private Pending? _pending;

    private sealed class Pending
    {
        public required VehicleCommand Command;
        public required MAVLink.mavlink_command_long_t Request;
        public DateTime Deadline;
        public int RetriesLeft;
        public TimeSpan Timeout;
    }

    public VehicleCommands(MAVLinkInterface mav, byte sysid, byte compid, Action<CommandUpdate> update,
                           TimeSpan? timeout = null, TimeSpan? armTimeout = null, int retries = 3)
    {
        _mav = mav;
        _sysid = sysid;
        _compid = compid;
        _update = update;
        _timeout = timeout ?? TimeSpan.FromSeconds(2);        // MP doCommand
        _armTimeout = armTimeout ?? TimeSpan.FromSeconds(10); // MP doCommand, COMPONENT_ARM_DISARM
        _retries = retries;
    }

    /// <summary>The flight modes of this vehicle's firmware, as MP names them (cs.mode uses the same names).</summary>
    public static IReadOnlyList<string> ModesOf(MAVLinkInterface mav)
    {
        try
        {
            return MissionPlanner.ArduPilot.Common.getModesList(mav.MAV.cs.firmware)?.Select(m => m.Value).ToList()
                   ?? new List<string>();
        }
        catch
        {
            return new List<string>();
        }
    }

    public bool Busy => _pending != null;

    /// <summary>Starts a command. Link thread only.</summary>
    public void Start(VehicleCommand cmd)
    {
        if (_pending != null)
        {
            _update(new CommandUpdate(cmd.Id, cmd.Kind, "error", $"busy: {_pending.Command.Kind} still waiting for the vehicle"));
            return;
        }

        MAVLink.mavlink_command_long_t req;
        TimeSpan timeout;
        switch (cmd.Kind)
        {
            case "arm":
            case "disarm":
                req = Request(MAVLink.MAV_CMD.COMPONENT_ARM_DISARM, cmd.Kind == "arm" ? 1 : 0, 0);
                timeout = _armTimeout;
                break;
            case "mode":
                // MP's translateMode fills the (deprecated) SET_MODE struct; only its numbers are used.
#pragma warning disable CS0612
                var mode = new MAVLink.mavlink_set_mode_t();
#pragma warning restore CS0612
                if (cmd.Mode == null || !_mav.translateMode(_sysid, _compid, cmd.Mode, ref mode))
                {
                    _update(new CommandUpdate(cmd.Id, cmd.Kind, "error", $"unknown mode '{cmd.Mode}' for this vehicle"));
                    return;
                }
                req = Request(MAVLink.MAV_CMD.DO_SET_MODE, mode.base_mode, mode.custom_mode);
                timeout = _timeout;
                break;
            default:
                _update(new CommandUpdate(cmd.Id, cmd.Kind, "error", $"unknown command '{cmd.Kind}'"));
                return;
        }

        _pending = new Pending
        {
            Command = cmd,
            Request = req,
            Deadline = DateTime.UtcNow + timeout,
            RetriesLeft = _retries,
            Timeout = timeout,
        };
        Send(req);
        _update(new CommandUpdate(cmd.Id, cmd.Kind, "sent", Describe(cmd)));
    }

    /// <summary>Retries and timeouts. Link thread, every pass.</summary>
    public void Tick()
    {
        var p = _pending;
        if (p == null || DateTime.UtcNow < p.Deadline)
            return;
        if (p.RetriesLeft > 0)
        {
            p.RetriesLeft--;
            p.Request.confirmation++;
            p.Deadline = DateTime.UtcNow + p.Timeout;
            Send(p.Request);
            return;
        }
        Finish("timeout", $"no answer from the vehicle ({_retries + 1} tries, {p.Timeout.TotalSeconds:0} s each)");
    }

    /// <summary>Every packet from the vehicle (inside readPacket, link thread).</summary>
    public void OnPacket(MAVLink.MAVLinkMessage msg)
    {
        var p = _pending;
        if (p == null || msg.msgid != (uint)MAVLink.MAVLINK_MSG_ID.COMMAND_ACK || msg.sysid != _sysid || msg.compid != _compid)
            return;
        var ack = msg.ToStructure<MAVLink.mavlink_command_ack_t>();
        if (ack.command != p.Request.command)
            return;
        var result = (MAVLink.MAV_RESULT)ack.result;
        if (result == MAVLink.MAV_RESULT.IN_PROGRESS)
        {
            p.Deadline = DateTime.UtcNow + p.Timeout;  // MP: restart the wait, no more retries
            p.RetriesLeft = 0;
            return;
        }
        if (result == MAVLink.MAV_RESULT.ACCEPTED)
            Finish("accepted", Describe(p.Command));
        else
            Finish("rejected", result.ToString());
    }

    /// <summary>The link dropped: the pending command's outcome is unknown.</summary>
    public void Abort(string reason)
    {
        if (_pending != null)
            Finish("error", $"{reason}: outcome unknown");
    }

    private void Finish(string status, string detail)
    {
        var cmd = _pending!.Command;
        _pending = null;
        _update(new CommandUpdate(cmd.Id, cmd.Kind, status, detail));
    }

    private MAVLink.mavlink_command_long_t Request(MAVLink.MAV_CMD command, float p1, float p2) => new()
    {
        target_system = _sysid,
        target_component = _compid,
        command = (ushort)command,
        confirmation = 0,
        param1 = p1,
        param2 = p2,
    };

    private void Send(MAVLink.mavlink_command_long_t req) =>
        _mav.generatePacket((int)MAVLink.MAVLINK_MSG_ID.COMMAND_LONG, req, _sysid, _compid);

    private static string Describe(VehicleCommand cmd) => cmd.Kind == "mode" ? $"mode {cmd.Mode}" : cmd.Kind;
}
