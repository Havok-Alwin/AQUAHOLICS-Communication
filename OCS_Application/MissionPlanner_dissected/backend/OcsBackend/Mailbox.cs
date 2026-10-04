// One WebSocket client of link A or link B: what waits to be sent, and the loop that sends it.
// Latest-wins per key, so a slow client never builds a backlog (MP's pending-update skip in
// FlightData.updateBindingSource), plus an ordered log that is never coalesced (STATUSTEXT).
using System.Net.WebSockets;

namespace Ocs.Backend;

internal sealed class Mailbox
{
    private readonly int _maxLog;
    private readonly object _lock = new();
    private readonly Dictionary<string, byte[]> _latest = new();
    private readonly Queue<byte[]> _log = new();
    private readonly SemaphoreSlim _signal = new(0);

    /// <param name="maxLog">Log entries a client may fall behind before it is disconnected.</param>
    public Mailbox(int maxLog) => _maxLog = maxLog;

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
            if (_log.Count >= _maxLog)
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

    /// <summary>Largest message accepted from a client; a larger one closes the connection.</summary>
    public const int MaxIncoming = 4096;

    /// <summary>
    /// Sends this mailbox on `ws` until the client closes, falls behind on the log, or `stop` fires.
    /// Each text message the client sends goes to `onMessage` (null: read and ignored).
    /// </summary>
    public async Task PumpAsync(WebSocket ws, CancellationToken stop, Action<string>? onMessage = null)
    {
        using var cts = CancellationTokenSource.CreateLinkedTokenSource(stop);
        var receive = ReceiveUntilClosed(ws, onMessage, cts.Token);
        _ = receive.ContinueWith(_ => cts.Cancel(), TaskScheduler.Default);  // closed: stop waiting
        try
        {
            while (!cts.IsCancellationRequested && ws.State == WebSocketState.Open && !receive.IsCompleted)
            {
                await WaitAsync(cts.Token);
                if (Overflowed)
                {
                    await ws.CloseAsync(WebSocketCloseStatus.PolicyViolation, "client too slow for the message log", CancellationToken.None);
                    break;
                }
                foreach (var msg in Drain())
                    await ws.SendAsync(msg, WebSocketMessageType.Text, true, cts.Token);
            }
        }
        catch (OperationCanceledException) { }
        catch (WebSocketException) { }
        finally
        {
            cts.Cancel();
        }

        // The client closed: answer its close frame, so it sees a clean close.
        if (ws.State == WebSocketState.CloseReceived)
        {
            try { await ws.CloseOutputAsync(WebSocketCloseStatus.NormalClosure, null, CancellationToken.None); }
            catch (WebSocketException) { }
        }
    }

    private static async Task ReceiveUntilClosed(WebSocket ws, Action<string>? onMessage, CancellationToken stop)
    {
        var buffer = new byte[MaxIncoming];
        var length = 0;
        try
        {
            while (ws.State == WebSocketState.Open)
            {
                var result = await ws.ReceiveAsync(buffer.AsMemory(length), stop);
                if (result.MessageType == WebSocketMessageType.Close)
                    return;
                length += result.Count;
                if (!result.EndOfMessage)
                {
                    if (length < buffer.Length)
                        continue;
                    await ws.CloseAsync(WebSocketCloseStatus.MessageTooBig, "message too big", CancellationToken.None);
                    return;
                }
                if (result.MessageType == WebSocketMessageType.Text)
                    onMessage?.Invoke(System.Text.Encoding.UTF8.GetString(buffer, 0, length));
                length = 0;
            }
        }
        catch (OperationCanceledException) { }
        catch (WebSocketException) { }
    }
}
