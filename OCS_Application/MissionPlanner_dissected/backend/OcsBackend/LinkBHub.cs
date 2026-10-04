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

namespace Ocs.Backend;

public sealed class LinkBHub : IDisposable
{
    public const int HistoryPerVehicle = 1000;
    public const int MaxQueuedLog = 5000;  // above the replayed history of both vehicles

    private readonly IReadOnlyList<VehicleLink> _links;
    private readonly object _lock = new();
    private readonly List<Client> _clients = new();
    private readonly Dictionary<string, byte[]> _params = new();
    private readonly Dictionary<string, Queue<byte[]>> _history = new();
    private readonly Dictionary<string, (float r, float p, float y)> _lastAtt = new();
    private readonly Dictionary<string, byte[]> _lastAttMsg = new();
    private readonly Timer _slowTimer, _backendTimer;

    public LinkBHub(IReadOnlyList<VehicleLink> links, TimeSpan? slowPeriod = null, TimeSpan? backendPeriod = null)
    {
        _links = links;
        foreach (var link in links)
        {
            _history[link.Name] = new Queue<byte[]>();
            link.PacketReceived += OnPacket;
            link.ParamsChanged += OnParams;
            link.StatusTextReceived += OnStatusText;
            link.StateChanged += (_, _) => PostBackend();
        }
        var slow = slowPeriod ?? TimeSpan.FromMilliseconds(500);  // LINK_B.slowHz = 2
        var backend = backendPeriod ?? TimeSpan.FromSeconds(1);
        _slowTimer = new Timer(_ => PostStatus(), null, slow, slow);
        _backendTimer = new Timer(_ => PostBackend(), null, backend, backend);
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

    private void PostBackend() => PostLatest("backend", LinkBMessages.Backend(LinkBMessages.Now(), _links));

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
        var client = new Client();
        lock (_lock)
        {
            client.PostLatest("backend", LinkBMessages.Backend(LinkBMessages.Now(), _links));
            foreach (var (name, msg) in _params)
                client.PostLatest($"params:{name}", msg);
            // FAST is on change only: without this, a still vehicle would show no attitude.
            foreach (var (name, msg) in _lastAttMsg)
                client.PostLatest($"att:{name}", msg);
            foreach (var history in _history.Values)
                foreach (var msg in history)
                    client.PostLog(msg);
            _clients.Add(client);
        }

        using var cts = CancellationTokenSource.CreateLinkedTokenSource(stop);
        var receive = ReceiveUntilClosed(ws, cts.Token);
        _ = receive.ContinueWith(_ => cts.Cancel(), TaskScheduler.Default);  // closed: stop waiting
        try
        {
            while (!cts.IsCancellationRequested && ws.State == WebSocketState.Open && !receive.IsCompleted)
            {
                await client.WaitAsync(cts.Token);
                if (client.Overflowed)
                {
                    await ws.CloseAsync(WebSocketCloseStatus.PolicyViolation, "client too slow for the message log", CancellationToken.None);
                    break;
                }
                foreach (var msg in client.Drain())
                    await ws.SendAsync(msg, WebSocketMessageType.Text, true, cts.Token);
            }
        }
        catch (OperationCanceledException) { }
        catch (WebSocketException) { }
        finally
        {
            lock (_lock)
                _clients.Remove(client);
            cts.Cancel();
        }

        // The client closed: answer its close frame, so the browser sees a clean close.
        if (ws.State == WebSocketState.CloseReceived)
        {
            try { await ws.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None); }
            catch (WebSocketException) { }
        }
    }

    // Reads (and for now ignores) what the frontend sends, so close frames are seen. Commands (logic 9)
    // will arrive here.
    private static async Task ReceiveUntilClosed(WebSocket ws, CancellationToken stop)
    {
        var buffer = new byte[4096];
        try
        {
            while (ws.State == WebSocketState.Open)
            {
                var result = await ws.ReceiveAsync(buffer, stop);
                if (result.MessageType == WebSocketMessageType.Close)
                    return;
            }
        }
        catch (OperationCanceledException) { }
        catch (WebSocketException) { }
    }

    /// <summary>One frontend's mailbox: latest-wins per key, plus the ordered STATUSTEXT log.</summary>
    internal sealed class Client
    {
        private readonly object _lock = new();
        private readonly Dictionary<string, byte[]> _latest = new();
        private readonly Queue<byte[]> _log = new();
        private readonly SemaphoreSlim _signal = new(0);

        public bool Overflowed { get; private set; }

        public void PostLatest(string key, byte[] msg)
        {
            lock (_lock)
                _latest[key] = msg;
            Signal();
        }

        public void PostLog(byte[] msg)
        {
            lock (_lock)
            {
                if (_log.Count >= MaxQueuedLog)
                    Overflowed = true;
                else
                    _log.Enqueue(msg);
            }
            Signal();
        }

        /// <summary>Everything waiting: the log in order, then the latest of each key.</summary>
        public List<byte[]> Drain()
        {
            lock (_lock)
            {
                var all = new List<byte[]>(_log.Count + _latest.Count);
                all.AddRange(_log);
                all.AddRange(_latest.Values);
                _log.Clear();
                _latest.Clear();
                return all;
            }
        }

        public Task WaitAsync(CancellationToken stop) => _signal.WaitAsync(stop);

        // At most one pending wake-up: the sender drains everything per wake-up anyway.
        private void Signal()
        {
            if (_signal.CurrentCount == 0)
                _signal.Release();
        }
    }

    public void Dispose()
    {
        _slowTimer.Dispose();
        _backendTimer.Dispose();
    }
}
