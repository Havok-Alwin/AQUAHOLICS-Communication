// Bridge: hosts frontend/ over HTTP, streams MAVLink telemetry (SSE) and accepts commands (POST).
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Threading;
using MissionPlanner;
using MissionPlanner.Comms;

static class Bridge
{
    static MAVLinkInterface mav;
    static readonly object gate = new object();
    static readonly List<HttpListenerResponse> clients = new List<HttpListenerResponse>();
    static string lastError = "";
    static bool connected;
    static string portName = "";

    static int Main(string[] args)
    {
        int port = args.Length > 0 ? int.Parse(args[0]) : 8080;
        string root = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "frontend"));
        var http = new HttpListener();
        http.Prefixes.Add("http://localhost:" + port + "/");
        http.Start();
        Console.WriteLine("Frontend: http://localhost:" + port + "/   (serving " + root + ")");

        new Thread(TelemetryLoop) { IsBackground = true }.Start();
        while (true)
        {
            var ctx = http.GetContext();
            ThreadPool.QueueUserWorkItem(_ => { try { Handle(ctx, root); } catch (Exception e) { Console.WriteLine(e.Message); try { ctx.Response.Abort(); } catch { } } });
        }
    }

    static void Handle(HttpListenerContext ctx, string root)
    {
        var path = ctx.Request.Url.AbsolutePath;
        if (path == "/events") { lock (gate) { ctx.Response.ContentType = "text/event-stream"; ctx.Response.Headers["Cache-Control"] = "no-cache"; ctx.Response.SendChunked = true; clients.Add(ctx.Response); } return; }
        if (path == "/api/ports") { Json(ctx, "[" + string.Join(",", SerialPort.GetPortNames().Select(Q)) + "]"); return; }
        if (path == "/api/connect") { var q = ctx.Request.QueryString; Json(ctx, Connect(q["port"], int.Parse(q["baud"] ?? "57600"))); return; }
        if (path == "/api/disconnect") { Json(ctx, Disconnect()); return; }
        if (path == "/api/arm") { Json(ctx, Arm(ctx.Request.QueryString["on"] == "1")); return; }

        if (path == "/") path = "/index.html";
        var file = Path.GetFullPath(Path.Combine(root, path.TrimStart('/')));
        if (!file.StartsWith(root) || !File.Exists(file)) { ctx.Response.StatusCode = 404; ctx.Response.Close(); return; }
        var ext = Path.GetExtension(file);
        ctx.Response.ContentType = ext == ".html" ? "text/html" : ext == ".js" ? "application/javascript" : ext == ".css" ? "text/css" : "application/octet-stream";
        var bytes = File.ReadAllBytes(file);
        ctx.Response.OutputStream.Write(bytes, 0, bytes.Length);
        ctx.Response.Close();
    }

    static string Connect(string port, int baud)
    {
        lock (gate)
        {
            try
            {
                if (connected) Disconnect();
                var sp = new SerialPort { PortName = port, BaudRate = baud };
                mav = new MAVLinkInterface();
                mav.BaseStream = sp;
                mav.Open(false, true, false);   // getparams=false, skipconnectedcheck, showui=false (no dialog)
                // ask the autopilot to stream attitude/position/status data
                foreach (var s in new[] { MAVLink.MAV_DATA_STREAM.ALL })
                    try { mav.requestDatastream(s, 4); } catch { }
                connected = true; portName = port; lastError = "";
                return "{\"ok\":true}";
            }
            catch (Exception e) { Console.WriteLine(e.ToString()); connected = false; lastError = e.Message; try { mav?.Close(); } catch { } return "{\"ok\":false,\"error\":" + Q(e.Message) + "}"; }
        }
    }

    static string Disconnect()
    {
        lock (gate) { try { mav?.Close(); } catch { } connected = false; return "{\"ok\":true}"; }
    }

    static string Arm(bool on)
    {
        try { if (!connected) throw new Exception("not connected"); bool r = mav.doARM(on); return "{\"ok\":" + (r ? "true" : "false") + "}"; }
        catch (Exception e) { return "{\"ok\":false,\"error\":" + Q(e.Message) + "}"; }
    }

    static void TelemetryLoop()
    {
        while (true)
        {
            Thread.Sleep(100);
            string msg;
            lock (gate) msg = connected ? Snapshot() : "{\"connected\":false,\"error\":" + Q(lastError) + "}";
            List<HttpListenerResponse> snap; lock (gate) snap = clients.ToList();
            var data = Encoding.UTF8.GetBytes("data: " + msg + "\n\n");
            foreach (var c in snap)
                try { c.OutputStream.Write(data, 0, data.Length); c.OutputStream.Flush(); }
                catch { lock (gate) clients.Remove(c); try { c.Abort(); } catch { } }
        }
    }

    static string Snapshot()
    {
        var cs = mav.MAV.cs;
        var inv = System.Globalization.CultureInfo.InvariantCulture;
        Func<double, string> n = v => double.IsNaN(v) || double.IsInfinity(v) ? "0" : v.ToString("0.###", inv);
        return "{\"connected\":true,\"port\":" + Q(portName) +
            ",\"name\":" + Q("sys" + mav.MAV.sysid + "-" + mav.MAV.aptype) +
            ",\"armed\":" + (cs.armed ? "true" : "false") +
            ",\"mode\":" + Q(cs.mode) +
            ",\"roll\":" + n(cs.roll) + ",\"pitch\":" + n(cs.pitch) + ",\"yaw\":" + n(cs.yaw) +
            ",\"airspeed\":" + n(cs.airspeed) + ",\"groundspeed\":" + n(cs.groundspeed) +
            ",\"alt\":" + n(cs.alt) + ",\"climb\":" + n(cs.verticalspeed) +
            ",\"batt_v\":" + n(cs.battery_voltage) + ",\"batt_a\":" + n(cs.current) + ",\"batt_pct\":" + cs.battery_remaining +
            ",\"gps_fix\":" + n(cs.gpsstatus) + ",\"sats\":" + n(cs.satcount) + ",\"hdop\":" + n(cs.gpshdop) +
            ",\"ekf\":" + n(cs.ekfstatus) + ",\"msg\":" + Q(cs.messageHigh) + "}";
    }

    static void Json(HttpListenerContext c, string s)
    {
        var b = Encoding.UTF8.GetBytes(s); c.Response.ContentType = "application/json";
        c.Response.OutputStream.Write(b, 0, b.Length); c.Response.Close();
    }
    static string Q(string s) => "\"" + (s ?? "").Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", " ").Replace("\n", " ") + "\"";
}
