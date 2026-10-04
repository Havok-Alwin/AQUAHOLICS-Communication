// Link B messages, backend -> frontend, as JSON text frames. The shapes match
// frontend/src/lib/telemetry.ts (LinkBMsg); `t` is the backend clock, Unix ms.
//   att        {ch:'att', vehicle, t, r, p, y}                 FAST, degrees, on every ATTITUDE (logic 0)
//   status     {ch:'status', vehicle, t, cs:{...}}             SLOW, 2 Hz, only while the link is LIVE (logic 8)
//   params     {ch:'params', vehicle, params:{BATT_*...}}       logic 6
//   statustext {ch:'statustext', vehicle, t, severity, text}   logic 7
//   backend    {ch:'backend', t, links:{USV1:{state, error}}}  1 Hz and on every link change
//   modes      {ch:'modes', vehicle, modes:[...]}               logic 9: on connect (MP's names)
//   cmdack     {ch:'cmdack', vehicle, id, cmd, status, detail, t} logic 9: every command update
// Frontend -> backend (logic 9):
//   cmd        {ch:'cmd', id, vehicle, cmd:'arm'|'disarm'|'mode', mode?}
using System.Reflection;
using System.Text.Json;
using MissionPlanner;

namespace Ocs.Backend;

public static class LinkBMessages
{
    /// <summary>
    /// The CurrentState fields the SLOW snapshot carries: frontend/src/lib/currentState.ts
    /// (CurrentStateFields), same names, same order. A test checks the two lists match.
    /// </summary>
    public static readonly string[] SlowFields =
    {
        "armed", "mode", "failsafe", "safetyactive", "prearmstatus", "ekfstatus", "landed_state",
        "groundspeed", "airspeed", "verticalspeed", "groundcourse", "turnrate",
        "lat", "lng", "alt", "HomeAlt", "DistToHome",
        "wpno", "wp_dist", "nav_bearing", "nav_roll", "nav_pitch", "targetalt", "targetairspeed", "xtrack_error",
        "battery_voltage", "battery_remaining", "current",
        "gpsstatus", "satcount", "gpshdop",
        "linkqualitygcs", "load", "vibex", "vibey", "vibez",
        "messageHigh", "messageHighSeverity",
    };

    private static readonly PropertyInfo[] SlowProps = SlowFields
        .Select(f => typeof(CurrentState).GetProperty(f, BindingFlags.Public | BindingFlags.Instance)
                     ?? throw new MissingMemberException(nameof(CurrentState), f))
        .ToArray();

    public static long Now() => DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

    public static byte[] Attitude(string vehicle, long t, double r, double p, double y) => Write(w =>
    {
        w.WriteString("ch", "att");
        w.WriteString("vehicle", vehicle);
        w.WriteNumber("t", t);
        w.WriteNumber("r", Math.Round(r, 2));
        w.WriteNumber("p", Math.Round(p, 2));
        w.WriteNumber("y", Math.Round(y, 2));
    });

    /// <summary>
    /// SLOW snapshot. A field that cannot be read, or is NaN/infinite, is left out: the frontend
    /// shows a missing field as '—', never as a made-up number.
    /// </summary>
    public static byte[] Status(string vehicle, long t, CurrentState cs) => Write(w =>
    {
        w.WriteString("ch", "status");
        w.WriteString("vehicle", vehicle);
        w.WriteNumber("t", t);
        w.WriteStartObject("cs");
        for (var i = 0; i < SlowProps.Length; i++)
        {
            object? value;
            try { value = SlowProps[i].GetValue(cs); }
            catch { continue; }
            WriteValue(w, SlowFields[i], value);
        }
        w.WriteEndObject();
    });

    public static byte[] Params(string vehicle, IReadOnlyDictionary<string, float> values) => Write(w =>
    {
        w.WriteString("ch", "params");
        w.WriteString("vehicle", vehicle);
        w.WriteStartObject("params");
        foreach (var (name, value) in values)
            WriteValue(w, name, value);
        w.WriteEndObject();
    });

    public static byte[] StatusText(string vehicle, StatusText st) => Write(w =>
    {
        w.WriteString("ch", "statustext");
        w.WriteString("vehicle", vehicle);
        w.WriteNumber("t", st.T);
        w.WriteNumber("severity", st.Severity);
        w.WriteString("text", st.Text);
    });

    public static byte[] Modes(string vehicle, IReadOnlyList<string> modes) => Write(w =>
    {
        w.WriteString("ch", "modes");
        w.WriteString("vehicle", vehicle);
        w.WriteStartArray("modes");
        foreach (var m in modes)
            w.WriteStringValue(m);
        w.WriteEndArray();
    });

    public static byte[] CmdAck(string vehicle, CommandUpdate u, long t) => Write(w =>
    {
        w.WriteString("ch", "cmdack");
        w.WriteString("vehicle", vehicle);
        w.WriteString("id", u.Id);
        w.WriteString("cmd", u.Kind);
        w.WriteString("status", u.Status);
        w.WriteString("detail", u.Detail);
        w.WriteNumber("t", t);
    });

    public static byte[] Backend(long t, IEnumerable<VehicleLink> links) => Write(w =>
    {
        w.WriteString("ch", "backend");
        w.WriteNumber("t", t);
        w.WriteStartObject("links");
        foreach (var link in links)
        {
            w.WriteStartObject(link.Name);
            w.WriteString("state", link.State.ToString().ToLowerInvariant());
            if (link.Error is { } error)
                w.WriteString("error", error);
            else
                w.WriteNull("error");
            w.WriteEndObject();
        }
        w.WriteEndObject();
    });

    private static void WriteValue(Utf8JsonWriter w, string name, object? value)
    {
        switch (value)
        {
            case bool b: w.WriteBoolean(name, b); break;
            case string s: w.WriteString(name, s); break;
            case Enum e: w.WriteNumber(name, Convert.ToInt64(e)); break;
            case float f when float.IsFinite(f): w.WriteNumber(name, f); break;
            case double d when double.IsFinite(d): w.WriteNumber(name, d); break;
            case byte or sbyte or short or ushort or int or uint or long:
                w.WriteNumber(name, Convert.ToInt64(value)); break;
            // null, non-finite, anything else: left out
        }
    }

    private static byte[] Write(Action<Utf8JsonWriter> body)
    {
        var buffer = new System.Buffers.ArrayBufferWriter<byte>(256);
        using (var w = new Utf8JsonWriter(buffer))
        {
            w.WriteStartObject();
            body(w);
            w.WriteEndObject();
        }
        return buffer.WrittenSpan.ToArray();
    }
}
