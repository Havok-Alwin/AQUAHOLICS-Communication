// Link B, backend side: turns the vehicle links into link B messages and pushes them to every
// connected frontend over a WebSocket.
//
// Rules (CLAUDE.md, "Backend requirements found in the IL", and logic 8):
// - A slow client must not build a backlog. Per client, per vehicle, only the latest att, status
//   and params wait to be sent (MP's pending-update skip in FlightData.updateBindingSource).
// - STATUSTEXT is a log and is never coalesced: every entry is queued in order. A client that lets
//   MaxQueuedLog entries pile up is disconnected; on reconnect it gets the history again.
// - status is sent only while that vehicle's link is LIVE, so a frozen snapshot is never fresh.
// - A new client first gets: link states, each vehicle's params and last attitude, and the STATUSTEXT history
//   (last HistoryPerVehicle per vehicle, like MP's cs.messages), so a reloaded page is complete.
// - ATTITUDE is forwarded as it arrives (on change). Degrees, yaw 0..360, as CurrentState does it.
using System.Net.WebSockets;
using System.Text.Json;

namespace Ocs.Backend;

public sealed class LinkBHub : IDisposable
{
    public const int HistoryPerVehicle = 1000;
    public const int MaxQueuedLog = 5000;  // above the replayed history of both vehicles

    private readonly IReadOnlyList<VehicleLink> _links;
    private readonly object _lock = new();
    private readonly List<Mailbox> _clients = new();
    private readonly Dictionary<string, byte[]> _params = new();
    private readonly Dictionary<string, Queue<byte[]>> _history = new();
    private readonly Dictionary<string, (float r, float p, float y)> _lastAtt = new();
    private readonly Dictionary<string, byte[]> _lastAttMsg = new();
    private readonly Dictionary<string, byte[]> _modes = new();
    private readonly Timer _slowTimer, _backendTimer;

    private readonly VehicleSettings? _settings;
    private readonly Func<IReadOnlyList<SerialPortInfo>> _listPorts;
    private IReadOnlyList<SerialPortInfo> _ports = Array.Empty<SerialPortInfo>();

    /// <param name="settings">Where connect/disconnect from the display are saved (null: not saved).</param>
    /// <param name="listPorts">Serial port discovery (tests pass a fake).</param>
    public LinkBHub(IReadOnlyList<VehicleLink> links, TimeSpan? slowPeriod = null, TimeSpan? backendPeriod = null,
                    VehicleSettings? settings = null, Func<IReadOnlyList<SerialPortInfo>>? listPorts = null)
    {
        _links = links;
        _settings = settings;
        _listPorts = listPorts ?? (() => SerialPorts.List());
        RefreshPorts();
        foreach (var link in links)
        {
            _history[link.Name] = new Queue<byte[]>();
            link.PacketReceived += OnPacket;
            link.ParamsChanged += OnParams;
            link.StatusTextReceived += OnStatusText;
            link.StateChanged += (_, _) => PostBackend();
            link.ModesChanged += OnModes;
            link.CommandUpdated += OnCommandUpdate;
        }
        var slow = slowPeriod ?? TimeSpan.FromMilliseconds(500);  // LINK_B.slowHz = 2
        var backend = backendPeriod ?? TimeSpan.FromSeconds(1);
        _slowTimer = new Timer(_ => PostStatus(), null, slow, slow);
        _backendTimer = new Timer(_ => { RefreshPorts(); PostBackend(); }, null, backend, backend);
    }

    public int ClientCount { get { lock (_lock) return _clients.Count; } }

    // --- producers (link threads and timers) ---------------------------------------------------

    private void OnPacket(VehicleLink link, MAVLink.MAVLinkMessage msg)
    {
        if (msg.msgid != (uint)MAVLink.MAVLINK_MSG_ID.ATTITUDE)
            return;
        var att = msg.ToStructure<MAVLink.mavlink_attitude_t>();
        // Same conversion as CurrentState (rad -> deg, yaw < 0 -> +360).
        var r = att.roll * (float)(180 / Math.PI);
        var p = att.pitch * (float)(180 / Math.PI);
        var y = att.yaw * (float)(180 / Math.PI);
        if (y < 0)
            y += 360;
        lock (_lock)
        {
            if (_lastAtt.TryGetValue(link.Name, out var last) && last == (r, p, y))
                return;  // FAST is on change only
            _lastAtt[link.Name] = (r, p, y);
        }
        var json = LinkBMessages.Attitude(link.Name, LinkBMessages.Now(), r, p, y);
        lock (_lock)
            _lastAttMsg[link.Name] = json;
        PostLatest($"att:{link.Name}", json);
    }

    private void OnParams(VehicleLink link, IReadOnlyDictionary<string, float> values)
    {
        var msg = LinkBMessages.Params(link.Name, values);
        lock (_lock)
            _params[link.Name] = msg;
        PostLatest($"params:{link.Name}", msg);
    }

    private void OnStatusText(VehicleLink link, StatusText st)
    {
        var msg = LinkBMessages.StatusText(link.Name, st);
        lock (_lock)
        {
            var history = _history[link.Name];
            history.Enqueue(msg);
            while (history.Count > HistoryPerVehicle)
                history.Dequeue();
            foreach (var client in _clients)
                client.PostLog(msg);
        }
    }

    private void OnModes(VehicleLink link, IReadOnlyList<string> modes)
    {
        var msg = LinkBMessages.Modes(link.Name, modes);
        lock (_lock)
            _modes[link.Name] = msg;
        PostLatest($"modes:{link.Name}", msg);
    }

    // Logic 9: every command update reaches every operator display, in order (a log, never coalesced).
    private void OnCommandUpdate(VehicleLink link, CommandUpdate u)
    {
        var msg = LinkBMessages.CmdAck(link.Name, u, LinkBMessages.Now());
        lock (_lock)
            foreach (var client in _clients)
                client.PostLog(msg);
    }

    /// <summary>Every reply to a connect / disconnect from a display (also sent to the displays as cmdack).</summary>
    public event Action<VehicleLink, CommandUpdate>? ConnectReplied;

    private void RefreshPorts()
    {
        try { _ports = _listPorts(); }
        catch (Exception e) { Console.Error.WriteLine($"link B: port listing failed: {e.Message}"); }
    }

    /// <summary>
    /// A message from a frontend: `cmd` (logic 9) or `connect` / `disconnect` (choose a vehicle's port
    /// from the display). A malformed one is ignored (logged).
    /// </summary>
    internal void OnClientMessage(string text)
    {
        string? ch, id = null, vehicle = null, kind = null, mode = null, port = null;
        int? baud = null, sysid = null;
        try
        {
            using var doc = JsonDocument.Parse(text);
            var root = doc.RootElement;
            if (root.ValueKind != JsonValueKind.Object)
                return;
            ch = Str(root, "ch");
            id = Str(root, "id");
            vehicle = Str(root, "vehicle");
            kind = Str(root, "cmd");
            mode = Str(root, "mode");
            port = Str(root, "port");
            baud = Int(root, "baud");
            sysid = Int(root, "sysid");
        }
        catch (JsonException)
        {
            return;
        }
        if (ch is "connect" or "disconnect")
        {
            OnConnect(ch, id, vehicle, port, baud, sysid);
            return;
        }
        if (ch != "cmd")
            return;
        var link = _links.FirstOrDefault(l => l.Name == vehicle);
        if (link == null || string.IsNullOrEmpty(id) || id.Length > 64 || kind == null)
        {
            Console.Error.WriteLine($"link B: ignored a malformed command: {text[..Math.Min(text.Length, 200)]}");
            return;
        }
        Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {link.Name}: command {kind}{(mode != null ? " " + mode : "")} (id {id}) from the operator display");
        link.Submit(new VehicleCommand(id, kind, mode));
    }

    private static string? Str(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

    private static int? Int(JsonElement e, string name) =>
        e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var i) ? i : null;

    // Connect a vehicle slot to a port the backend listed, or disconnect it. Saved for the next start.
    private void OnConnect(string ch, string? id, string? vehicle, string? port, int? baud, int? sysid)
    {
        var link = _links.FirstOrDefault(l => l.Name == vehicle);
        if (link == null || string.IsNullOrEmpty(id) || id.Length > 64)
        {
            Console.Error.WriteLine($"link B: ignored a malformed {ch} (vehicle {vehicle}, id {id})");
            return;
        }
        void Reply(string status, string detail)
        {
            var update = new CommandUpdate(id, ch, status, detail);
            ConnectReplied?.Invoke(link, update);
            OnCommandUpdate(link, update);
        }

        if (ch == "disconnect")
        {
            Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {link.Name}: disconnect from the operator display");
            link.Configure(null);
            _settings?.Save(_links);  // Config already reads the pending (null) config
            Reply("accepted", "disconnected");
            return;
        }
        // Only a port the backend itself listed: the display cannot make it open any other file.
        if (port == null || !_ports.Any(p => p.Path == port))
        {
            Reply("error", $"unknown port '{port}' (not in the list of serial ports)");
            return;
        }
        if (baud is not { } b || Array.IndexOf(SerialPorts.Bauds, b) < 0)
        {
            Reply("error", $"unsupported baud rate {baud}");
            return;
        }
        if (sysid is { } id2 && (id2 < 1 || id2 > 255))
        {
            Reply("error", $"sysid {sysid} out of range 1..255");
            return;
        }
        var other = _links.FirstOrDefault(l => l != link && l.Config?.Port == port);
        if (other != null)
        {
            Reply("error", $"{port} is already used by {other.Name}");
            return;
        }
        Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {link.Name}: connect {port} @ {b}{(sysid != null ? $" sysid {sysid}" : "")} from the operator display");
        link.Configure(new VehicleLinkConfig(link.Name, port, b, sysid is { } s ? (byte)s : null));
        _settings?.Save(_links);
        Reply("accepted", $"connecting to {port}");
    }

    private void PostStatus()
    {
        foreach (var link in _links)
        {
            var mav = link.Mav;
            if (link.State != LinkState.Live || mav == null)
                continue;
            byte[] msg;
            try { msg = LinkBMessages.Status(link.Name, LinkBMessages.Now(), mav.MAV.cs); }
            catch (Exception e) { Console.Error.WriteLine($"link B: {link.Name} status failed: {e.Message}"); continue; }
            PostLatest($"status:{link.Name}", msg);
        }
    }

    private void PostBackend() => PostLatest("backend", LinkBMessages.Backend(LinkBMessages.Now(), _links, _ports));

    private void PostLatest(string key, byte[] msg)
    {
        lock (_lock)
            foreach (var client in _clients)
                client.PostLatest(key, msg);
    }

    // --- clients ----------------------------------------------------------------------------------

    /// <summary>Serves one frontend until it closes, falls behind on the log, or `stop` fires.</summary>
    public async Task ServeAsync(WebSocket ws, CancellationToken stop)
    {
        var client = new Mailbox(MaxQueuedLog);
        lock (_lock)
        {
            client.PostLatest("backend", LinkBMessages.Backend(LinkBMessages.Now(), _links, _ports));
            foreach (var (name, msg) in _params)
                client.PostLatest($"params:{name}", msg);
            // FAST is on change only: without this, a still vehicle would show no attitude.
            foreach (var (name, msg) in _lastAttMsg)
                client.PostLatest($"att:{name}", msg);
            foreach (var (name, msg) in _modes)
                client.PostLatest($"modes:{name}", msg);
            foreach (var history in _history.Values)
                foreach (var msg in history)
                    client.PostLog(msg);
            _clients.Add(client);
        }

        try
        {
            await client.PumpAsync(ws, stop, OnClientMessage);
        }
        finally
        {
            lock (_lock)
                _clients.Remove(client);
        }
    }

    public void Dispose()
    {
        _slowTimer.Dispose();
        _backendTimer.Dispose();
    }
}
