// Backend server (Kestrel): link B WebSocket at /linkb, link A WebSocket at /linka (LinkA.cs, for
// ../main.py), and, if found, the frontend's built files (frontend/dist) at /. Same origin in
// deployment, so the kiosk page needs no CORS.
// Binds to localhost by default: the operator display runs on the OCS laptop.
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.FileProviders;
using Microsoft.Extensions.Logging;

namespace Ocs.Backend;

public static class LinkBServer
{
    public const string DefaultUrl = "http://127.0.0.1:5080";
    public const string Path = "/linkb";

    /// <summary>The Vite dev server (npm run dev) proxies /linkb from these pages.</summary>
    public static readonly string[] DevOrigins = { "http://127.0.0.1:5173", "http://localhost:5173" };

    /// <summary>
    /// Logic 9: link B carries commands, so a browser may connect only from the operator display:
    /// the page this server serves, or the Vite dev server. Without this, any web page open in the
    /// kiosk browser could arm a vehicle (WebSockets are not limited by the same-origin policy).
    /// A request with no Origin header is not from a browser page (e.g. a local test client).
    /// </summary>
    public static bool OriginAllowed(string? origin, HttpRequest request)
    {
        if (string.IsNullOrEmpty(origin))
            return true;
        if (DevOrigins.Contains(origin, StringComparer.OrdinalIgnoreCase))
            return true;
        return Uri.TryCreate(origin, UriKind.Absolute, out var o)
               && string.Equals(o.Scheme, request.Scheme, StringComparison.OrdinalIgnoreCase)
               && string.Equals(o.Authority, request.Host.Value, StringComparison.OrdinalIgnoreCase);
    }

    public static WebApplication Build(LinkBHub hub, string url, string? webRoot, CancellationToken stop,
                                       LinkAHub? linkA = null)
    {
        var builder = WebApplication.CreateSlimBuilder();
        builder.WebHost.UseUrls(url);
        builder.Logging.SetMinimumLevel(LogLevel.Warning);
        var app = builder.Build();

        app.UseWebSockets(new WebSocketOptions { KeepAliveInterval = TimeSpan.FromSeconds(5) });
        app.Map(Path, async (HttpContext ctx) =>
        {
            if (!ctx.WebSockets.IsWebSocketRequest)
            {
                ctx.Response.StatusCode = StatusCodes.Status400BadRequest;
                return;
            }
            var origin = ctx.Request.Headers.Origin.ToString();
            if (!OriginAllowed(origin, ctx.Request))
            {
                Console.Error.WriteLine($"link B: refused a WebSocket from origin {origin}");
                ctx.Response.StatusCode = StatusCodes.Status403Forbidden;
                return;
            }
            using var ws = await ctx.WebSockets.AcceptWebSocketAsync();
            await hub.ServeAsync(ws, stop);
        });

        if (linkA != null)
        {
            app.Map(LinkAMessages.Path, async (HttpContext ctx) =>
            {
                if (!ctx.WebSockets.IsWebSocketRequest)
                {
                    ctx.Response.StatusCode = StatusCodes.Status400BadRequest;
                    return;
                }
                using var ws = await ctx.WebSockets.AcceptWebSocketAsync();
                await linkA.ServeAsync(ws, stop);
            });
        }

        if (webRoot != null)
        {
            var files = new PhysicalFileProvider(webRoot);
            app.UseDefaultFiles(new DefaultFilesOptions { FileProvider = files });
            app.UseStaticFiles(new StaticFileOptions { FileProvider = files });
        }
        return app;
    }

    /// <summary>
    /// frontend/dist with an index.html, searched upwards from the working directory and from the
    /// executable (dotnet run from backend/OcsBackend, or the published folder). Null if not built.
    /// </summary>
    public static string? FindFrontend()
    {
        foreach (var start in new[] { Directory.GetCurrentDirectory(), AppContext.BaseDirectory })
        {
            for (var dir = new DirectoryInfo(start); dir != null; dir = dir.Parent)
            {
                var dist = System.IO.Path.Combine(dir.FullName, "frontend", "dist");
                if (File.Exists(System.IO.Path.Combine(dist, "index.html")))
                    return dist;
            }
        }
        return null;
    }
}
