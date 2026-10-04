// A serial port that is always open, never delivers data and captures everything MP writes.
using MissionPlanner.Comms;

namespace Ocs.Backend.Tests;

public sealed class FakeSerial : ICommsSerial
{
    private readonly MemoryStream _written = new();

    public bool IsOpen { get; set; } = true;

    /// <summary>Every MAVLink packet written so far, parsed.</summary>
    public List<MAVLink.MAVLinkMessage> Packets()
    {
        var parser = new MAVLink.MavlinkParse();
        var stream = new MemoryStream(_written.ToArray());
        var packets = new List<MAVLink.MAVLinkMessage>();
        while (stream.Position < stream.Length)
            packets.Add(parser.ReadPacket(stream));
        return packets;
    }

    public void Clear() => _written.SetLength(0);

    public void Write(byte[] buffer, int offset, int count) => _written.Write(buffer, offset, count);
    public void Write(string text) => throw new NotSupportedException();
    public void WriteLine(string text) => throw new NotSupportedException();

    public Stream BaseStream => _written;
    public int BaudRate { get; set; } = 57600;
    public int BytesToRead => 0;
    public int BytesToWrite => 0;
    public int DataBits { get; set; } = 8;
    public bool DtrEnable { get; set; }
    public string PortName { get; set; } = "fake";
    public int ReadBufferSize { get; set; }
    public int ReadTimeout { get; set; }
    public bool RtsEnable { get; set; }
    public int WriteBufferSize { get; set; }
    public int WriteTimeout { get; set; }

    public void Open() => IsOpen = true;
    public void Close() => IsOpen = false;
    public void Dispose() => IsOpen = false;
    public void DiscardInBuffer() { }
    public int Read(byte[] buffer, int offset, int count) => 0;
    public int ReadByte() => -1;
    public int ReadChar() => -1;
    public string ReadExisting() => "";
    public string ReadLine() => "";
    public void toggleDTR() { }
}
