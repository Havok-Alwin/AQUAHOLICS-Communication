// Phase 3: mission upload/download to the Pixhawk, the same non-blocking shape as VehicleCommands.cs
// (logic 9).
//
// MP (decompiled): MAVLinkInterface.getWP(Async)/setWP(Async) and the static
// MissionPlanner.ArduPilot.mav_mission.download/upload are built on giveComport + readPacketAsync()
// (MP reads packets itself) -- the same blocking problem VehicleCommands.cs's header explains for
// doCommand: it would add a second reader on the port, or stall the link loop (and our GCS
// heartbeat) for as long as a whole mission transfer takes. So mission upload/download is its own
// hand-rolled state machine here, run on the link thread from VehicleLink's queue, exactly like
// logic 9: the vehicle's replies arrive through the link's normal read loop.
//
// Protocol (MAVLink messages, confirmed from MAVLink.dll's IL): MISSION_COUNT (44),
// MISSION_REQUEST_INT (51, legacy MISSION_REQUEST 40 also accepted), MISSION_ITEM_INT (73, legacy
// MISSION_ITEM 39 also accepted), MISSION_ACK (47), MISSION_REQUEST_LIST (43).
// - Write (upload): GCS sends MISSION_COUNT{count} -> vehicle requests each seq in turn via
//   MISSION_REQUEST_INT -> GCS replies MISSION_ITEM_INT for that seq -> after the last item the
//   vehicle sends MISSION_ACK{type} (0 = MAV_MISSION_ACCEPTED).
// - Read (download): GCS sends MISSION_REQUEST_LIST -> vehicle replies MISSION_COUNT{count} -> GCS
//   requests each seq via MISSION_REQUEST_INT -> vehicle replies MISSION_ITEM_INT per seq -> GCS
//   sends a final MISSION_ACK{ACCEPTED}.
//
// The frontend's Waypoint (lib/waypoints.ts: cmd, lat, lng, alt, p[0..3], frame) already lines up
// 1:1 with mavlink_mission_item_int_t's command, x(lat*1e7), y(lng*1e7), z(alt), param1..4, frame.
// Home is item 0 (frame 0), exactly like the existing .waypoints file format (toWpl/fromWpl).
using MissionPlanner;

namespace Ocs.Backend;

/// <summary>One mission item, mirroring mavlink_mission_item_int_t (x/y = lat/lng * 1e7 on the wire).</summary>
public sealed record MissionItem(ushort Seq, byte Frame, ushort Cmd, float P1, float P2, float P3, float P4, double Lat, double Lng, float Alt);

/// <summary>A mission job from the operator display. Kind: "mission_write" or "mission_read". Items set only for write (home at seq 0).</summary>
public sealed record MissionJob(string Id, string Kind, IReadOnlyList<MissionItem>? Items = null);

/// <summary>
/// Progress of a mission transfer. Status: "sent" (waiting for the vehicle), then zero or more
/// "progress" (Current/Total climbing), then one final status: "accepted", "rejected" (Detail =
/// MAV_MISSION_RESULT), "timeout", or "error" (never sent: not live, busy, malformed...).
/// </summary>
public sealed record MissionUpdate(string Id, string Kind, string Status, string Detail, int Current = 0, int Total = 0);

/// <summary>The downloaded mission (mission_read), raised once on a successful read. Item 0 is home if its Frame is 0.</summary>
public sealed record MissionResult(IReadOnlyList<MissionItem> Items);

internal sealed class MissionCommands
{
    public static readonly string[] Kinds = { "mission_write", "mission_read" };

    private readonly MAVLinkInterface _mav;
    private readonly byte _sysid, _compid;
    private readonly Action<MissionUpdate> _update;
    private readonly Action<MissionResult> _result;
    private readonly TimeSpan _timeout;
    private readonly int _retries;
    private Pending? _pending;

    private sealed class Pending
    {
        public required MissionJob Job;
        public Action Resend = () => { };
        /// <summary>Write: items the vehicle has been sent so far (for the final status). Read: unused.</summary>
        public int Next;
        /// <summary>Read: the item count once MISSION_COUNT arrives; null until then.</summary>
        public int? Total;
        /// <summary>Read: items collected so far, in order.</summary>
        public List<MissionItem>? Received;
        public DateTime Deadline;
        public int RetriesLeft;
    }

    public MissionCommands(MAVLinkInterface mav, byte sysid, byte compid, Action<MissionUpdate> update,
                           Action<MissionResult> result, TimeSpan? timeout = null, int retries = 3)
    {
        _mav = mav;
        _sysid = sysid;
        _compid = compid;
        _update = update;
        _result = result;
        _timeout = timeout ?? TimeSpan.FromSeconds(2);
        _retries = retries;
    }

    public bool Busy => _pending != null;

    /// <summary>Starts a mission transfer. Link thread only.</summary>
    public void Start(MissionJob job)
    {
        if (_pending != null)
        {
            _update(new MissionUpdate(job.Id, job.Kind, "error", $"busy: {_pending.Job.Kind} still waiting for the vehicle"));
            return;
        }

        switch (job.Kind)
        {
            case "mission_write":
            {
                var items = job.Items ?? Array.Empty<MissionItem>();
                var count = (ushort)items.Count;
                var p = new Pending { Job = job };
                _pending = p;
                Arm(p, () => SendCount(count));
                _update(new MissionUpdate(job.Id, job.Kind, "sent", $"writing {items.Count} item(s)", 0, items.Count));
                return;
            }
            case "mission_read":
            {
                var p = new Pending { Job = job, Received = new List<MissionItem>() };
                _pending = p;
                Arm(p, SendRequestList);
                _update(new MissionUpdate(job.Id, job.Kind, "sent", "requesting the mission", 0, 0));
                return;
            }
            default:
                _update(new MissionUpdate(job.Id, job.Kind, "error", $"unknown command '{job.Kind}'"));
                return;
        }
    }

    /// <summary>Sends (or resends) one step, and arms the timeout/retry for it.</summary>
    private void Arm(Pending p, Action send)
    {
        p.Resend = send;
        send();
        p.Deadline = DateTime.UtcNow + _timeout;
        p.RetriesLeft = _retries;
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
            p.Resend();
            p.Deadline = DateTime.UtcNow + _timeout;
            return;
        }
        Finish("timeout", $"no answer from the vehicle ({_retries + 1} tries, {_timeout.TotalSeconds:0} s each)");
    }

    /// <summary>Every packet from the vehicle (inside readPacket, link thread).</summary>
    public void OnPacket(MAVLink.MAVLinkMessage msg)
    {
        var p = _pending;
        if (p == null || msg.sysid != _sysid || msg.compid != _compid)
            return;
        if (p.Job.Kind == "mission_write")
            OnWritePacket(p, msg);
        else
            OnReadPacket(p, msg);
    }

    private void OnWritePacket(Pending p, MAVLink.MAVLinkMessage msg)
    {
        var items = p.Job.Items ?? Array.Empty<MissionItem>();
        if (msg.msgid == (uint)MAVLink.MAVLINK_MSG_ID.MISSION_ACK)
        {
            var result = (MAVLink.MAV_MISSION_RESULT)msg.ToStructure<MAVLink.mavlink_mission_ack_t>().type;
            if (result == MAVLink.MAV_MISSION_RESULT.MAV_MISSION_ACCEPTED)
                Finish("accepted", $"{items.Count} item(s) written");
            else
                Finish("rejected", result.ToString());
            return;
        }

        int seq;
        if (msg.msgid == (uint)MAVLink.MAVLINK_MSG_ID.MISSION_REQUEST_INT)
            seq = msg.ToStructure<MAVLink.mavlink_mission_request_int_t>().seq;
        else if (msg.msgid == (uint)MAVLink.MAVLINK_MSG_ID.MISSION_REQUEST)
#pragma warning disable CS0612  // legacy fallback for a vehicle that only speaks MISSION_REQUEST
            seq = msg.ToStructure<MAVLink.mavlink_mission_request_t>().seq;
#pragma warning restore CS0612
        else
            return;
        if (seq >= items.Count)
            return;  // a stray/out-of-range request: ignore rather than fail the transfer

        // A request for an already-sent seq (the vehicle re-asking) is served the same way: just
        // resend that item, not an error.
        p.Next = seq + 1;
        var item = items[seq];
        Arm(p, () => SendItem(item));
        _update(new MissionUpdate(p.Job.Id, p.Job.Kind, "progress", $"{seq + 1}/{items.Count} uploaded", seq + 1, items.Count));
    }

    private void OnReadPacket(Pending p, MAVLink.MAVLinkMessage msg)
    {
        if (msg.msgid == (uint)MAVLink.MAVLINK_MSG_ID.MISSION_COUNT)
        {
            if (p.Total != null)
                return;  // already have the count
            var count = msg.ToStructure<MAVLink.mavlink_mission_count_t>().count;
            p.Total = count;
            if (count == 0)
            {
                SendAck();
                _result(new MissionResult(Array.Empty<MissionItem>()));
                Finish("accepted", "0 item(s) read");
                return;
            }
            Arm(p, () => SendRequestInt(0));
            _update(new MissionUpdate(p.Job.Id, p.Job.Kind, "progress", $"0/{count} downloaded", 0, count));
            return;
        }

        if (msg.msgid != (uint)MAVLink.MAVLINK_MSG_ID.MISSION_ITEM_INT && msg.msgid != (uint)MAVLink.MAVLINK_MSG_ID.MISSION_ITEM)
            return;
        if (p.Total is not { } total)
            return;  // an item before we know the count: ignore
        var received = p.Received!;

        MissionItem item;
        if (msg.msgid == (uint)MAVLink.MAVLINK_MSG_ID.MISSION_ITEM_INT)
        {
            var m = msg.ToStructure<MAVLink.mavlink_mission_item_int_t>();
            if (m.seq != received.Count)
                return;  // out of order or a duplicate: ignore, Tick() re-requests
            item = new MissionItem(m.seq, m.frame, m.command, m.param1, m.param2, m.param3, m.param4, m.x / 1e7, m.y / 1e7, m.z);
        }
        else
        {
#pragma warning disable CS0612  // legacy fallback for a vehicle that only speaks MISSION_ITEM
            var m = msg.ToStructure<MAVLink.mavlink_mission_item_t>();
#pragma warning restore CS0612
            if (m.seq != received.Count)
                return;
            item = new MissionItem(m.seq, m.frame, m.command, m.param1, m.param2, m.param3, m.param4, m.x, m.y, m.z);
        }
        received.Add(item);

        if (received.Count < total)
        {
            Arm(p, () => SendRequestInt((ushort)received.Count));
            _update(new MissionUpdate(p.Job.Id, p.Job.Kind, "progress", $"{received.Count}/{total} downloaded", received.Count, total));
        }
        else
        {
            SendAck();
            _result(new MissionResult(received.ToArray()));
            Finish("accepted", $"{total} item(s) read");
        }
    }

    /// <summary>The link dropped: the pending transfer's outcome is unknown.</summary>
    public void Abort(string reason)
    {
        if (_pending != null)
            Finish("error", $"{reason}: outcome unknown");
    }

    private void Finish(string status, string detail)
    {
        var p = _pending!;
        var current = p.Job.Kind == "mission_write" ? p.Next : p.Received?.Count ?? 0;
        var total = p.Job.Kind == "mission_write" ? (p.Job.Items?.Count ?? 0) : (p.Total ?? 0);
        _pending = null;
        _update(new MissionUpdate(p.Job.Id, p.Job.Kind, status, detail, current, total));
    }

    private void SendCount(ushort count) => Send(MAVLink.MAVLINK_MSG_ID.MISSION_COUNT, new MAVLink.mavlink_mission_count_t
    {
        target_system = _sysid, target_component = _compid, count = count, mission_type = 0,
    });

    private void SendRequestList() => Send(MAVLink.MAVLINK_MSG_ID.MISSION_REQUEST_LIST, new MAVLink.mavlink_mission_request_list_t
    {
        target_system = _sysid, target_component = _compid, mission_type = 0,
    });

    private void SendRequestInt(ushort seq) => Send(MAVLink.MAVLINK_MSG_ID.MISSION_REQUEST_INT, new MAVLink.mavlink_mission_request_int_t
    {
        target_system = _sysid, target_component = _compid, seq = seq, mission_type = 0,
    });

    private void SendAck() => Send(MAVLink.MAVLINK_MSG_ID.MISSION_ACK, new MAVLink.mavlink_mission_ack_t
    {
        target_system = _sysid, target_component = _compid, type = (byte)MAVLink.MAV_MISSION_RESULT.MAV_MISSION_ACCEPTED, mission_type = 0,
    });

    private void SendItem(MissionItem item) => Send(MAVLink.MAVLINK_MSG_ID.MISSION_ITEM_INT, new MAVLink.mavlink_mission_item_int_t
    {
        target_system = _sysid, target_component = _compid,
        seq = item.Seq, frame = item.Frame, command = item.Cmd,
        current = (byte)(item.Seq == 0 ? 1 : 0), autocontinue = 1,
        param1 = item.P1, param2 = item.P2, param3 = item.P3, param4 = item.P4,
        x = (int)Math.Round(item.Lat * 1e7), y = (int)Math.Round(item.Lng * 1e7), z = item.Alt,
        mission_type = 0,
    });

    private void Send(MAVLink.MAVLINK_MSG_ID id, object payload) =>
        _mav.generatePacket((int)id, payload, _sysid, _compid);
}
