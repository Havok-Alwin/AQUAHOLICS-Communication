// Link B server (Kestrel): the WebSocket at /linkb and, if found, the frontend's built files
// (frontend/dist) at /. Same origin in deployment, so the kiosk page needs no CORS.
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

    public static WebApplication Build(LinkBHub hub, string url, string? webRoot, CancellationToken stop)
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
            using var ws = await ctx.WebSockets.AcceptWebSocketAsync();
            await hub.ServeAsync(ws, stop);
        });

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
