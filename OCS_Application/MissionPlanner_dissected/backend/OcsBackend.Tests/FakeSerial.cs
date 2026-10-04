// A serial port that captures everything MP writes and delivers whatever a test (or FakeVehicle)
// feeds into it.
using MissionPlanner.Comms;

namespace Ocs.Backend.Tests;

public sealed class FakeSerial : ICommsSerial
{
    private readonly MemoryStream _written = new();
    private readonly Queue<byte> _input = new();
    private readonly object _lock = new();
    private volatile bool _isOpen;

    public FakeSerial(bool open = true) => _isOpen = open;

    public bool IsOpen => _isOpen;

    /// <summary>Open() throws, like a missing device.</summary>
    public bool FailOpen { get; set; }

    /// <summary>Simulates the device going away (e.g. USB unplugged): the port reports closed.</summary>
    public void Unplug() => _isOpen = false;

    /// <summary>Bytes the "vehicle" sent, readable by MP.</summary>
    public void Feed(byte[] bytes)
    {
        lock (_lock)
            foreach (var b in bytes)
                _input.Enqueue(b);
    }

    /// <summary>Every MAVLink packet written so far, parsed.</summary>
    public List<MAVLink.MAVLinkMessage> Packets()
    {
        byte[] bytes;
        lock (_lock)
            bytes = _written.ToArray();
        var parser = new MAVLink.MavlinkParse();
        var stream = new MemoryStream(bytes);
        var packets = new List<MAVLink.MAVLinkMessage>();
        while (stream.Position < stream.Length)
        {
            // null: a packet the parser rejects (MP's Open sends one signed STATUSTEXT).
            if (parser.ReadPacket(stream) is { } packet)
                packets.Add(packet);
        }
        return packets;
    }

    public void Clear()
    {
        lock (_lock)
            _written.SetLength(0);
    }

    public void Write(byte[] buffer, int offset, int count)
    {
        lock (_lock)
            _written.Write(buffer, offset, count);
    }

    public void Write(string text) => throw new NotSupportedException();
    public void WriteLine(string text) => throw new NotSupportedException();

    public int BytesToRead { get { lock (_lock) return _input.Count; } }

    public int Read(byte[] buffer, int offset, int count)
    {
        lock (_lock)
        {
            var n = 0;
            while (n < count && _input.Count > 0)
                buffer[offset + n++] = _input.Dequeue();
            return n;
        }
    }

    public int ReadByte()
    {
        lock (_lock)
            return _input.Count > 0 ? _input.Dequeue() : -1;
    }

    public void DiscardInBuffer()
    {
        lock (_lock)
            _input.Clear();
    }

    public void Open()
    {
        if (FailOpen)
            throw new IOException("no such device");
        _isOpen = true;
    }

    public void Close() => _isOpen = false;
    public void Dispose() => _isOpen = false;

    public Stream BaseStream => _written;
    public int BaudRate { get; set; } = 57600;
    public int BytesToWrite => 0;
    public int DataBits { get; set; } = 8;
    public bool DtrEnable { get; set; }
    public string PortName { get; set; } = "fake";
    public int ReadBufferSize { get; set; }
    public int ReadTimeout { get; set; }
    public bool RtsEnable { get; set; }
    public int WriteBufferSize { get; set; }
    public int WriteTimeout { get; set; }
    public int ReadChar() => ReadByte();
    public string ReadExisting() => "";
    public string ReadLine() => "";
    public void toggleDTR() { }
}
