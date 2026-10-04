// Vehicle backend entry point.
// `dotnet run` (no arguments): serves the operator display on http://127.0.0.1:5080 with one slot
// per vehicle (USV1, UAV1). Connect each one from the display (port list, Connect / Disconnect /
// Reconnect); the choice is saved (VehicleSettings) and restored at the next start.
// `--vehicle NAME=PORT[@BAUD][#SYSID]` (repeatable): connect a slot at start instead (not saved).
// `--http URL` (default http://127.0.0.1:5080). `--web DIR|none` the built frontend (default:
// frontend/dist found upwards). `--check`: the .NET 10 load check only (Phase A), then exit.
// `--extra-port PATH` (repeatable, testing): offer a port the discovery does not list (e.g. a
// simulator's /dev/pts/N).
// Prints link state changes, battery params, vehicle messages, commands, and once a second the
// state and roll/pitch/yaw of each connected vehicle.
using System.Reflection;
using MissionPlanner;
using MissionPlanner.Comms;

namespace Ocs.Backend;

public static class Program
{
    public static int Main(string[] args)
    {
        Console.WriteLine($"Runtime: {System.Runtime.InteropServices.RuntimeInformation.FrameworkDescription}");

        foreach (var name in new[] { "MissionPlanner.ArduPilot", "MissionPlanner.Comms", "MissionPlanner.Utilities", "MAVLink", "Interfaces" })
        {
            var asm = Assembly.Load(name);
            Console.WriteLine($"Loaded {asm.GetName().Name} {asm.GetName().Version}");
        }

        // Logic 3: before any CurrentState exists, so MP's built-in re-request stays off.
        StreamRates.DisableMpRerequest();

        // Construct what the backend needs. Constructors pull in most transitive dependencies.
        var mav = new MAVLinkInterface();
        var serial = new SerialPort();
        var cs = mav.MAV.cs;
        Console.WriteLine($"MAVLinkInterface OK, CurrentState OK (multiplierspeed={CurrentState.multiplierspeed}, multiplieralt={CurrentState.multiplieralt}, multiplierdist={CurrentState.multiplierdist})");
        Console.WriteLine($"SerialPort OK, ports: [{string.Join(", ", SerialPort.GetPortNames())}]");
        if (!StreamRates.MpRerequestDisabled(cs))
        {
            Console.Error.WriteLine("MP's built-in stream re-request is still on (cs.rate* not -1).");
            return 1;
        }
        Console.WriteLine("MP built-in stream re-request off (cs.rate* = -1). Our start rates: "
            + string.Join(", ", StreamRates.StartRates.Select(r => $"{r.Key} {r.Value}")));

        // Units are display-only (logic 10): CurrentState's multipliers are process-wide and feed link A's
        // heartbeats to RoboCommand, so anything but 1 would publish wrong speeds and altitudes.
        if (CurrentState.multiplierspeed != 1 || CurrentState.multiplieralt != 1 || CurrentState.multiplierdist != 1)
        {
            Console.Error.WriteLine("CurrentState unit multipliers are not 1: refusing to start (heartbeats would be wrong).");
            return 1;
        }

        _ = serial;
        mav.Dispose();

        var vehicles = new List<VehicleLinkConfig>();
        var extraPorts = new List<SerialPortInfo>();
        var check = false;
        var url = LinkBServer.DefaultUrl;
        var web = LinkBServer.FindFrontend();
        for (var i = 0; i < args.Length; i += 2)
        {
            if (args[i] == "--check")
            {
                check = true;
                i--;  // no value
                continue;
            }
            var value = i + 1 < args.Length ? args[i + 1] : null;
            switch (args[i])
            {
                case "--vehicle" when value != null && ParseVehicle(value) is { } config:
                    vehicles.Add(config);
                    break;
                case "--extra-port" when value != null:
                    extraPorts.Add(new SerialPortInfo(value, $"{Path.GetFileName(value)} (extra)"));
                    break;
                case "--http" when value != null:
                    url = value;
                    break;
                case "--web" when value != null:
                    web = value == "none" ? null : Path.GetFullPath(value);
                    break;
                default:
                    Console.Error.WriteLine("Usage: OcsBackend [--vehicle NAME=PORT[@BAUD][#SYSID]]... [--http URL] [--web DIR|none] [--extra-port PATH]... [--check]");
                    return 2;
            }
        }

        if (check)
        {
            Console.WriteLine("Phase A: .NET 10 load check passed (no hardware).");
            return 0;
        }
        return RunVehicles(vehicles, url, web, extraPorts);
    }

    // NAME=PORT[@BAUD][#SYSID], e.g. USV1=/dev/ttyUSB0@57600#1
    private static VehicleLinkConfig? ParseVehicle(string spec)
    {
        var eq = spec.IndexOf('=');
        if (eq <= 0)
            return null;
        var name = spec[..eq];
        var rest = spec[(eq + 1)..];

        byte? sysid = null;
        var hash = rest.LastIndexOf('#');
        if (hash >= 0)
        {
            if (!byte.TryParse(rest[(hash + 1)..], out var id))
                return null;
            sysid = id;
            rest = rest[..hash];
        }

        var baud = 57600;
        var at = rest.LastIndexOf('@');
        if (at >= 0)
        {
            if (!int.TryParse(rest[(at + 1)..], out baud))
                return null;
            rest = rest[..at];
        }

        return rest.Length == 0 ? null : new VehicleLinkConfig(name, rest, baud, sysid);
    }

    /// <summary>The vehicle slots the display shows (frontend/src/lib/config.ts VEHICLES).</summary>
    private static readonly string[] Slots = { "USV1", "UAV1" };

    private static int RunVehicles(List<VehicleLinkConfig> configs, string url, string? web, List<SerialPortInfo> extraPorts)
    {
        using var stop = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; stop.Cancel(); };

        // One slot per vehicle: --vehicle wins, else the saved connection, else OFF (connect from the display).
        var settings = new VehicleSettings();
        var saved = configs.Count == 0 ? settings.Load() : new Dictionary<string, VehicleSettings.Entry?>();
        var links = Slots.Concat(configs.Select(c => c.Name)).Distinct().Select(name =>
        {
            var config = configs.FirstOrDefault(c => c.Name == name)
                         ?? (saved.TryGetValue(name, out var e) && e != null ? new VehicleLinkConfig(name, e.Port, e.Baud, e.SysId) : null);
            if (config != null)
                Console.WriteLine($"{name}: {(configs.Any(c => c.Name == name) ? "from --vehicle" : $"restored from {settings.Path}")}: {config.Port} @ {config.Baud}");
            return new VehicleLink(name, config);
        }).ToList();
        using var hub = new LinkBHub(links, settings: configs.Count == 0 ? settings : null,
                                     listPorts: () => SerialPorts.List().Concat(extraPorts).ToList());
        hub.ConnectReplied += (l, u) =>
            Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {l.Name}: {u.Kind} {u.Status.ToUpperInvariant()}: {u.Detail}");
        using var linkA = new LinkAHub(links);
        var app = LinkBServer.Build(hub, url, web, stop.Token, linkA);
        try { app.StartAsync().GetAwaiter().GetResult(); }
        catch (IOException e)
        {
            Console.Error.WriteLine($"Link B: cannot listen on {url}: {e.Message} (another backend running?)");
            return 3;
        }
        // Kestrel's host takes over SIGINT/SIGTERM: stop everything when it stops.
        app.Lifetime.ApplicationStopping.Register(stop.Cancel);
        Console.WriteLine($"Link A (OCS): ws{url[4..]}{LinkAMessages.Path}");
        Console.WriteLine($"Link B: ws{url[4..]}{LinkBServer.Path}"
                          + (web != null ? $"; frontend {url}/ from {web}" : "; no frontend served (not built?)"));
        Console.WriteLine($"Operator display: {url}/  (connect the vehicles there)");
        var threads = links.Select(link =>
        {
            link.StateChanged += (l, s) =>
                Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {l.Name}: {s}{(l.Error is { } e ? $" ({e})" : "")}");
            link.ParamsChanged += (l, p) =>
                Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {l.Name}: params "
                                  + string.Join(", ", p.Select(kv => $"{kv.Key}={kv.Value}")));
            link.CommandUpdated += (l, u) =>
                Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {l.Name}: command {u.Kind} (id {u.Id}) {u.Status.ToUpperInvariant()}: {u.Detail}");
            link.StatusTextReceived += (l, st) =>
                Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {l.Name}: [{(MAVLink.MAV_SEVERITY)st.Severity}] {st.Text}");
            var t = new Thread(() => link.Run(stop.Token)) { Name = link.Name, IsBackground = true };
            t.Start();
            return t;
        }).ToList();

        while (!stop.Token.WaitHandle.WaitOne(1000))
        {
            foreach (var link in links.Where(l => l.State != LinkState.Off))
            {
                var mav = link.Mav;
                var line = $"{DateTime.Now:HH:mm:ss} {link.Name} {link.State}";
                if (mav != null)
                {
                    var c = mav.MAV.cs;
                    line += $"  silent {link.Silence?.TotalSeconds:0.0} s  roll {c.roll:0.0}  pitch {c.pitch:0.0}  yaw {c.yaw:0.0}"
                            + $"  mode {c.mode}  armed {c.armed}  linkq {c.linkqualitygcs}%";
                }
                Console.WriteLine(line);
            }
        }

        app.StopAsync().GetAwaiter().GetResult();
        foreach (var t in threads)
            t.Join(TimeSpan.FromSeconds(5));
        return 0;
    }
}
