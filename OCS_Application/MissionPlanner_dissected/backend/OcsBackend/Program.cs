// Vehicle backend entry point.
// Phase A: the .NET 10 check without hardware. Load the MP DLLs and construct the objects the
// backend will use. The hardware half (connect to the Pixhawk, print roll/pitch/yaw) runs on the
// test bench with `--port`.
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
        Console.WriteLine("Phase A: .NET 10 load check passed (no hardware).");
        return 0;
    }
}
