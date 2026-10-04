// Vehicle backend entry point.
// No arguments: Phase A, the .NET 10 check without hardware. Load the MP DLLs and construct the
// objects the backend will use.
// `--vehicle NAME=PORT[@BAUD][#SYSID]` (repeatable): connects each vehicle (logic 8), serves
// link B and the frontend (LinkBServer), and prints link state changes, battery params, vehicle
// messages and, once a second, the link state and roll/pitch/yaw.
// `--http URL` link B address (default http://127.0.0.1:5080). `--web DIR` the built frontend
// (default: frontend/dist found upwards; `--web none` serves link B only).
// Example: dotnet run -- --vehicle USV1=/dev/ttyACM0@115200#1
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

        _ = serial;
        mav.Dispose();

        var vehicles = new List<VehicleLinkConfig>();
        var url = LinkBServer.DefaultUrl;
        var web = LinkBServer.FindFrontend();
        for (var i = 0; i < args.Length; i += 2)
        {
            var value = i + 1 < args.Length ? args[i + 1] : null;
            switch (args[i])
            {
                case "--vehicle" when value != null && ParseVehicle(value) is { } config:
                    vehicles.Add(config);
                    break;
                case "--http" when value != null:
                    url = value;
                    break;
                case "--web" when value != null:
                    web = value == "none" ? null : Path.GetFullPath(value);
                    break;
                default:
                    Console.Error.WriteLine("Usage: OcsBackend [--vehicle NAME=PORT[@BAUD][#SYSID]]... [--http URL] [--web DIR|none]");
                    return 2;
            }
        }

        if (vehicles.Count == 0)
        {
            Console.WriteLine("Phase A: .NET 10 load check passed (no hardware).");
            return 0;
        }
        return RunVehicles(vehicles, url, web);
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

    private static int RunVehicles(List<VehicleLinkConfig> configs, string url, string? web)
    {
        using var stop = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; stop.Cancel(); };

        var links = configs.Select(c => new VehicleLink(c)).ToList();
        using var hub = new LinkBHub(links);
        var app = LinkBServer.Build(hub, url, web, stop.Token);
        try { app.StartAsync().GetAwaiter().GetResult(); }
        catch (IOException e)
        {
            Console.Error.WriteLine($"Link B: cannot listen on {url}: {e.Message} (another backend running?)");
            return 3;
        }
        // Kestrel's host takes over SIGINT/SIGTERM: stop everything when it stops.
        app.Lifetime.ApplicationStopping.Register(stop.Cancel);
        Console.WriteLine($"Link B: ws{url[4..]}{LinkBServer.Path}"
                          + (web != null ? $"; frontend {url}/ from {web}" : "; no frontend served (not built?)"));
        var threads = links.Select(link =>
        {
            link.StateChanged += (l, s) =>
                Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {l.Name}: {s}{(l.Error is { } e ? $" ({e})" : "")}");
            link.ParamsChanged += (l, p) =>
                Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {l.Name}: params "
                                  + string.Join(", ", p.Select(kv => $"{kv.Key}={kv.Value}")));
            link.StatusTextReceived += (l, st) =>
                Console.WriteLine($"{DateTime.Now:HH:mm:ss.fff} {l.Name}: [{(MAVLink.MAV_SEVERITY)st.Severity}] {st.Text}");
            var t = new Thread(() => link.Run(stop.Token)) { Name = link.Name, IsBackground = true };
            t.Start();
            return t;
        }).ToList();

        while (!stop.Token.WaitHandle.WaitOne(1000))
        {
            foreach (var link in links)
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
