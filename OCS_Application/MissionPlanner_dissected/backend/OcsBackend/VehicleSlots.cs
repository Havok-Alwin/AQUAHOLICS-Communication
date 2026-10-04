// Connect from the display: the serial ports the operator can choose, and the connections to
// restore at the next start.
using System.Text.Json;

namespace Ocs.Backend;

public sealed record SerialPortInfo(string Path, string Label);

public static class SerialPorts
{
    /// <summary>Baud rates offered on the display (SiK radios: 57600; USB ignores it).</summary>
    public static readonly int[] Bauds = { 9600, 19200, 38400, 57600, 111100, 115200, 230400, 460800, 500000, 921600, 1500000 };

    /// <summary>
    /// Ports a vehicle can be on. Linux: /dev/serial/by-id first (stable across replugs, named after
    /// the device: "ArduPilot Pixhawk1 (ttyACM0)"), then any other ttyACM/ttyUSB. Windows: COM ports.
    /// </summary>
    public static IReadOnlyList<SerialPortInfo> List(string byIdDir = "/dev/serial/by-id", string devDir = "/dev")
    {
        if (OperatingSystem.IsWindows())
            return MissionPlanner.Comms.SerialPort.GetPortNames().Order().Select(p => new SerialPortInfo(p, p)).ToList();

        var ports = new List<SerialPortInfo>();
        var covered = new HashSet<string>();
        try
        {
            foreach (var link in Directory.GetFiles(byIdDir).Order())
            {
                var target = ResolveName(link);
                covered.Add(target);
                ports.Add(new SerialPortInfo(link, $"{DeviceName(System.IO.Path.GetFileName(link))} ({target})"));
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        try
        {
            foreach (var dev in Directory.GetFiles(devDir, "tty*").Order())
            {
                var name = System.IO.Path.GetFileName(dev);
                if ((name.StartsWith("ttyACM") || name.StartsWith("ttyUSB")) && !covered.Contains(name))
                    ports.Add(new SerialPortInfo(dev, name));
            }
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
        return ports;
    }

    /// <summary>usb-ArduPilot_Pixhawk1_25001B000551333532383533-if00 -> ArduPilot Pixhawk1</summary>
    public static string DeviceName(string byIdName)
    {
        var name = byIdName.StartsWith("usb-") ? byIdName[4..] : byIdName;
        var parts = name.Split('_');
        return parts.Length > 1 ? string.Join(' ', parts[..^1]) : name;
    }

    private static string ResolveName(string link)
    {
        try
        {
            var target = new FileInfo(link).ResolveLinkTarget(returnFinalTarget: true);
            return target?.Name ?? System.IO.Path.GetFileName(link);
        }
        catch (IOException)
        {
            return System.IO.Path.GetFileName(link);
        }
    }
}

/// <summary>
/// The last connection per vehicle, saved when the operator connects or disconnects and restored at
/// the next start, so a restarted backend reconnects by itself. JSON: {"USV1": {"port", "baud",
/// "sysid"}, "UAV1": null}.
/// </summary>
public sealed class VehicleSettings
{
    public sealed record Entry(string Port, int Baud, byte? SysId);

    private readonly string _path;
    private readonly object _lock = new();

    public VehicleSettings(string? path = null)
    {
        _path = path ?? System.IO.Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "aquaholics-ocs", "vehicles.json");
    }

    public string Path => _path;

    public Dictionary<string, Entry?> Load()
    {
        lock (_lock)
        {
            try
            {
                return JsonSerializer.Deserialize<Dictionary<string, Entry?>>(File.ReadAllText(_path)) ?? new();
            }
            catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
            {
                return new();
            }
        }
    }

    public void Save(IEnumerable<VehicleLink> links)
    {
        var data = links.ToDictionary(l => l.Name,
            l => l.Config is { } c ? new Entry(c.Port, c.Baud, c.ExpectedSysId) : null);
        lock (_lock)
        {
            try
            {
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(_path)!);
                File.WriteAllText(_path, JsonSerializer.Serialize(data, new JsonSerializerOptions { WriteIndented = true }));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                Console.Error.WriteLine($"could not save the vehicle connections to {_path}: {e.Message}");
            }
        }
    }
}
