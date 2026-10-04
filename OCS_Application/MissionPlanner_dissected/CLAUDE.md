# MissionPlanner_dissected

Operator frontend for the AQUAHOLICS RobotX 2026 System of Systems. One screen serves two
independent sources:

| Source | Backend | Role |
|---|---|---|
| Vehicles (USV1, UAV1) | `backend/`: Mission Planner backend DLLs (`MissionPlanner.ArduPilot.dll` + deps) | MAVLink link, `CurrentState` telemetry, commands |
| RoboCommand | `../main.py` (the OCS, Python, MQTT + protobuf) | Competition protocol: course, run declaration, heartbeats, task reports, Task 4 |

```
Pixhawk (ArduPilot) --radio/MAVLink--> backend (MP DLLs, .NET 10)
                                          |-- link A: heartbeat data --> ../main.py (OCS) --MQTT--> RoboCommand
                                          '-- link B: telemetry/commands <--> frontend <-- link C: OCS status
```

## Status

- `backend/`: MP DLLs, `Bridge.cs` (throwaway Mono test, not the base) and `OcsBackend/`, the real
  vehicle backend (.NET 10 console project). Phase A is done: the MP DLLs load on .NET 10 without
  hardware (`dotnet run` in `backend/OcsBackend`). Logic 3 (stream rates, Phase B) is built:
  `StreamRates.cs`, tested in `backend/OcsBackend.Tests` (`dotnet test`, xunit, fake serial port).
  Logic 8 is built: `VehicleLink.cs` (connect, read loop, link-lost, reconnect, STATUSTEXT and
  battery-param events), run by `Program.cs --vehicle ...`. Link B phase 1 (backend) is built:
  `LinkBMessages.cs`, `LinkBHub.cs`, `LinkBServer.cs` (Kestrel on `http://127.0.0.1:5080`: WebSocket
  `/linkb` + `frontend/dist`). Link B phase 2 (frontend client, `lib/linkB.ts`) is built, so real
  mode shows live vehicle data end to end. Link A is built (`LinkA.cs`, `RobotState.cs`; OCS side
  `../vehicle_link.py`): real mode publishes RobotX heartbeats from real telemetry.
- `frontend/`: Svelte 5 + TS + Vite. The shell (layout, source/staleness model, mock-mode banner) and
  all frontend logic items (0, 1, 2, 5, 6, 7, 10, 11) are done. Link B and link C transports are done.
  In mock mode, `src/lib/mock.ts` feeds link B messages, and that file is excluded from production
  builds.
- Tech stack: frontend decided. See the "Tech stack" section below.

## Next step

Review the Task 4 replies (logic 3 and 8 are committed; their rows still say "review"). All logic items except 4 are done, and every panel of the display is built. Remaining work:
- A base layer for the map: a georeferenced image of the course or self-made tiles, as
  `frontend/public/map/base.json` + the image (see "Map").
- Bench: `dotnet run -- --vehicle USV1=<port>@<baud>#<sysid>` against the real Pixhawk (USB, then
  radio) is the .NET 10 hardware check.
- Logic 4 is covered by logic 0 (rAF frame clock instead of invalidate-on-change). Close it on review.

The .NET 10 check (a small console app that loads `backend/MissionPlanner.ArduPilot.dll`, connects
to the Pixhawk on USB and prints roll/pitch/yaw) is deferred to test-bench time. Mono is the fallback only if it fails.

## Reference material (outside this folder)

- `~/MissionPlanner-latest/`: the full Mission Planner build (binaries only, no C# source). Read its
  logic from the IL: `ikdasm ~/MissionPlanner-latest/MissionPlanner.exe > mp.il` (FlightData,
  MainV2), `MissionPlanner.Controls.dll` (HUD), `MissionPlanner.ArduPilot.dll` (CurrentState,
  MAVLinkInterface). Upstream source: github.com/ArduPilot/MissionPlanner. For long methods,
  decompile instead: `dotnet tool install ilspycmd --tool-path <dir>`, then
  `ilspycmd -t MissionPlanner.Controls.HUD MissionPlanner.Controls.dll` (gives readable C#).
- `~/Test_frontend/`: prototype of logic 0 (per-stream rates, fast/slow channel, rAF frame clock,
  extrapolation) in `backend/Bridge.cs` and `frontend/app.js`. Reference only; not the base.
- `../RobotX Resources.pdf`: OCS <-> RoboCommand protocol (topics, envelopes, heartbeat, checklist).
- `../../RobotX Resources_2.pdf`: the four mission tasks.
- `../README.md` and `../main.py`: the OCS, already validated. Change it only additively.

## Workflow rules

- Claude does not run `git commit` or `git push`. After each change, Claude gives the exact
  `git add <files>` command; the user adds, commits and pushes.
- One logic item (table below) per change, so each one can be reviewed alone.
- Plan before building; build in phases; stop for review between phases.

## Competition constraints (from the RobotX 2026 handbook)

- OCS publishes at most 5 messages/s per system to RoboCommand. OCS heartbeat is 2 Hz (`HEARTBEAT_HZ`).
- The operator display must show RoboCommand connection state and command receipt/response state.
- Heartbeat content: robot state (killed/manual/auto), position, speed, heading, roll, pitch,
  altitude/depth, current task, vehicle type, UAV flight phase.
- Course networks are isolated: assume **no Internet**. No CDN scripts, no online map tiles.
- Task 4: keep-out areas and a moving object (stay > 10 m away, "as visible on the tracking display").

## Safety rules for the frontend

- The OCS (`../main.py`) is the only MQTT publisher. The frontend never talks to RoboCommand directly.
- No simulated data in real mode.
- Each source fails independently: if one backend is down, its panel shows OFFLINE with the age of
  the last update. A stale value is never displayed as live.
- Start read-only; add commands one at a time, each with confirmation where it can move a vehicle.

## Logic to extract from Mission Planner (read from the IL of the 1.3.x build)

Port the logic, not the WinForms code. Status: `todo` / `done`.

| # | Logic | Source in MP | What it does | Status |
|---|---|---|---|---|
| 0 | Rendering pipeline (our own, tested in `~/Test_frontend`) | not MP | Per-stream rates instead of `ALL @ 4 Hz`; fast channel (roll/pitch/yaw, pushed on change, ~20 Hz) vs slow channel (status, 2 Hz); draw on a fixed frame clock (`requestAnimationFrame`), extrapolate with velocity from the last two samples, light 15 ms ease. Simulation at 20 Hz: RMS error 1.4 deg -> 0.7 deg, biggest frame jump 4.7 -> 1.8 deg. At 4 Hz no smoothing helps, so the data rate is the real fix. Frontend: `frameClock.ts`, `attitude.ts`, `telemetry.ts` (FAST/SLOW message types), `Hud.svelte`. Liveness comes from SLOW, because FAST is sent on change only. Backend side (per-stream rates) is logic 3 | done |
| 1 | Binding map | `FlightData.InitializeComponent` (72 `Binding`s) | HUD field <- `CurrentState` field, e.g. heading<-yaw, status<-armed, message<-messageHigh, gpsfix<-gpsstatus, batterylevel<-battery_voltage, navroll<-nav_roll, targetheading<-nav_bearing, disttowp<-wp_dist, groundalt<-HomeAlt, plus ekfstatus, prearmstatus, failsafe, linkqualitygcs, vibex/y/z. The 72 bindings are 70 fixed in `InitializeComponent` plus 2 user-configurable QuickViews; all 6 BindingSources point at `CurrentState`. Ported subset with reasons for each exclusion: `bindings.ts`. Field names and types from the ArduPilot DLL IL: `currentState.ts`. The SLOW message carries `cs: Partial<CurrentStateFields>` and replaces the snapshot, never merges it | done |
| 2 | Update gate | `CurrentState.UpdateCurrentSettings`, `FlightData.updateBindingSource` | **Corrected from the IL:** the 50 ms gate in `UpdateCurrentSettings` covers only housekeeping; the UI-push callback runs every call. The real UI gate is `FlightData.updateBindingSource`: at most every 100 ms, it skips while a previous UI update is still pending (5 s watchdog), and it only pushes bindings for the visible tab. Frontend: `gate.ts` (100 ms, keeps the latest value, `cancel()`) applied to SLOW snapshots in `telemetry.ts`. Freshness (`markUpdate`) is not gated. Backend requirements: see "Backend requirements found in the IL" | done |
| 3 | Stream-rate setup | `MAVLinkInterface.requestDatastream`; `cs.rateattitude/rateposition/ratestatus/ratesensors/raterc` (defaults 4/2/2/2) | Requests each MAVLink message group at its own rate. **MP (decompiled):** REQUEST_DATA_STREAM (msg 66) sent twice; skipped if the group's marker message already arrives at the rate, within (hz−1, hz+0.1] over the last 2 s (markers: SYS_STATUS, ATTITUDE, VFR_HUD, AHRS, GLOBAL_POSITION_INT, RC_CHANNELS_RAW, RAW_IMU); `hz = -1` means skip. MP's built-in re-request (every 38 s, see "Backend requirements") sends EXTRA2 at the *attitude* rate. **Decided 2026-10-04:** MP method through the DLL, our own 8 s / 30 s re-request (so EXTRA2 keeps its rate); start rates EXTRA1 20, EXTENDED_STATUS 2, POSITION 2, EXTRA2 2, EXTRA3 2, RAW_SENSORS 0, RC_CHANNELS 0 (tune on the bench once the radio data rate is known); test against a fake serial port that captures our packets (no SITL). **Built:** `StreamRates.cs`. MP's built-in re-request is switched off by setting the static `CurrentState.rate*backup` to -1 before any `MAVLinkInterface` exists (every `CurrentState` copies them in its constructor; `requestDatastream` and `CameraProtocol.RequestMessageIntervals` skip -1); `Program.cs` asserts it. `Tick()`: first call with an open port and a known sysid requests every group through MP's `requestDatastream` (so MP's per-group skip applies); then a re-request once any group has been off its rate (MP's own test, ported from the private `hzratecheck`) for 8 s, at most every 30 s. Rate 0 stops a group only if it is arriving. 8 tests in `OcsBackend.Tests/StreamRatesTests.cs` parse the REQUEST_DATA_STREAM bytes MP writes | done (review) |
| 4 | Invalidate on change | `HUD.set_roll` -> `Invalidate()` | MP's HUD is event-driven, not timer-driven (item 0 improves on this) | todo |
| 5 | HUD geometry | `HUD.doPaint` | Pitch-ladder px/deg, ticks, heading tape, aircraft symbol, colours. Ported from the decompiled source (`ilspycmd`, see Reference material) in `hudDraw.ts`. It is a pure draw function and every size scales with the canvas as in MP. The status-line items (battery, GPS, link bars, clock, vibe, EKF, pre-arm, CPU) are not drawn on the HUD because the vehicle card shows them. For the USV, the altitude tape and AS line are off. Deviations, each with its reason in the file header: heading-tape target off the right edge (fixes an MP bug), wrap-aware off-tape check, dark heading readout, and the stacked ARMED/DISARMED/SAFE/FAILSAFE texts (MP's overlap). Low-speed flags await logic 6 and units await logic 10 | done |
| 6 | Warning thresholds | `FlightData.mainloop` (battery), `HUD.doPaint` (colour rules) | When to alert the operator. Safety-relevant: ported exactly in `warnings.ts`. **Battery:** thresholds come from the vehicle's params (`BATT_LOW_VOLT`, `BATT_CRT_VOLT`, `BATT_LOW_MAH`/`BATT_CRT_MAH` ÷ `BATT_CAPACITY` × 100). Critical falls back to low. Low when voltage ≤ low volt **or** remaining < low %; critical likewise with the critical thresholds. With no params, the battery shows "limits unknown" instead of MP's compare-against-0. **Inline doPaint rules:** GPS fix 0/1 red (2D is not); EKF > 0.5 orange, > 0.8 red; any vibe axis > 30 orange, > 60 red; link 0 % red; CPU load 100 red; SAFE red; pre-arm only while disarmed. **Not ported:** `lowairspeed`/`lowgroundspeed`, which MP only ever sets false (low speed exists only as an optional speech alert). Note: `battery_remaining` -1 (unknown) counts as < % like in MP. Shown as warning chips (critical first) and the battery tile colour on the vehicle card | done |
| 7 | Status text severity | `cs.messageHigh`, `cs.messageHighSeverity` (`MAV_SEVERITY`); `MAVLinkInterface` STATUSTEXT (msg 253) | Colour/priority of vehicle messages. **Selection runs in the backend (MP DLL):** a STATUSTEXT becomes `messageHigh` if severity ≤ Settings `severity` (default 4 = WARNING), or if it starts with `PreArm:`/`Arm:`/`Tuning:` (then stamped EMERGENCY). `CurrentState` also sets it itself (fence breach, EKF/sensor health) as EMERGENCY. The getter reads back "" 10 s after it was set; identical text does not refresh it. Every STATUSTEXT goes to `cs.messages` (last 1000). **Frontend:** `severity.ts` (rules + colour: ≤3 red, 4 yellow, else white, shared with the HUD); card message line coloured by severity ("No vehicle message" when clear); collapsible "Messages (n)" log per vehicle (1000 kept, 50 shown, newest first) fed by a new link B `statustext` message | done |
| 8 | Connect / link-lost | `MainV2.doConnect`, `MAVLinkInterface.Open`, `MainV2.SerialReader` | Open link, request streams, detect lost heartbeat so a frozen display is not read as a still vehicle. **MP (decompiled):** `Open(getparams:false, showui:false)` waits up to `CONNECT_TIMEOUT_SECONDS` (30) for 2 heartbeats from one sysid with compid 1 (or 4 from any), skipping GCS heartbeats; with `showui:false` the `NoUIReporter` swallows every exception, so failure only shows as `BaseStream.IsOpen == false`. `SerialReader` reads while `BytesToRead > 10` (≤ 1 s per pass), then `UpdateCurrentSettings` per MAV, sends a GCS HEARTBEAT once a second, decays `linkqualitygcs` ×0.8/s after 1 s without a valid packet, and warns "No Data for N Seconds" after 3 s, only when armed and 30 s after connect. MP never reconnects by itself. **Phase 1 built:** `VehicleLink.cs`, one per vehicle and thread: CLOSED → CONNECTING (MP's `Open`) → LIVE / LOST. LOST = no valid packet from the autopilot (`MAV.lastvalidpacket`, per sysid/compid, so the radio's own RADIO_STATUS does not count) for 1 s, armed or not; the port stays open and LIVE returns by itself. Port closed, read error, failed connect → CLOSED, retry every 5 s on a fresh `MAVLinkInterface`. Optional expected sysid per vehicle refuses swapped radios. Loop per pass: read, `UpdateCurrentSettings`, `StreamRates.Tick()`, GCS heartbeat 1 Hz. **Link B contract:** send a vehicle's SLOW only while LIVE (frontend: STALE 2 s later, ~3 s total). Bench run: `dotnet run -- --vehicle USV1=/dev/ttyACM0@115200#1` prints state changes and roll/pitch/yaw each second (this is also the deferred .NET 10 hardware check). 7 tests in `VehicleLinkTests.cs` (scripted vehicle on the fake port, real time). **Phase 2 built:** `BatteryParams.cs` fetches the 5 battery params without blocking the loop (PARAM_REQUEST_READ for the missing names every 1 s, up to 5 rounds; answers come through the normal read loop) and raises `VehicleLink.ParamsChanged` once all arrived, or after 5 rounds with what did, then on every change (ArduPilot broadcasts PARAM_VALUE on any set). `VehicleLink.StatusTextReceived` raises every autopilot STATUSTEXT with severity and backend receive time (Unix ms); other components (e.g. a companion computer) are not forwarded, like MP. 4 more tests. **Left for link B:** turning these events, LIVE/LOST and the SLOW snapshot into link B messages | phases 1-2 done (review) |
| 9 | Commands | `MAVLinkInterface.doARM`, `setMode`, `doCommand`, `translateMode`, `Common.getModesList` | Arm/disarm, mode change; mode lists differ per vehicle type. **MP (decompiled):** `doARM` = `doCommand(COMPONENT_ARM_DISARM, p1 1/0)` (force: p2 2989/21196). `doCommand` with ack **reads packets itself** (`giveComport` pauses MP's reader): timeout 2 s, 10 s for arm/disarm, 3 retries with `confirmation`+1, IN_PROGRESS restarts the wait once and stops the retries, ACCEPTED = success. `setMode` looks the name up in `getModesList(cs.firmware)` (parameter metadata FLTMODE1 / MODE1), sends DO_SET_MODE without waiting, plus SET_MODE twice. **Built:** `VehicleCommands.cs`, run on the link thread from a queue (`VehicleLink.Submit`), non-blocking: the COMMAND_ACK comes through the normal read loop. Calling MP's `doCommand` would add a second reader on the port, or stall the loop (and our GCS heartbeat: vehicle GCS failsafe) for up to 4 x 10 s. MP's timeouts, retries, confirmation counter and IN_PROGRESS rule are kept. Mode change also waits for the DO_SET_MODE ack (no SET_MODE). No force arm/disarm. One command at a time per vehicle; only while LIVE; a dropped link reports "outcome unknown". Link B: `cmd` in, `cmdack` (sent, then accepted/rejected/timeout/error, to every display, in order) and `modes` (MP's names for the vehicle's firmware) out; the backend logs every command and result. The `/linkb` WebSocket now refuses browser pages from other origins (403; allowed: this server's own origin and the Vite dev server). **Frontend:** `VehicleCommands.svelte` on the vehicle card: ARM / DISARM / mode + Set mode, each through a confirmation dialog (Cancel focused) with warnings: arming in an autonomous mode, an autonomous mode while armed, disarming an AIRBORNE UAV. Disabled unless the vehicle is LIVE, no command is waiting, and the page is in real mode. Tests: `VehicleCommandTests.cs` (11). End-to-end 2026-10-04: headless Chrome clicked ARM, Set mode Auto and DISARM/Cancel on a fake boat through the real backend: armed, mode Auto, cancel sent nothing | done (review) |
| 10 | Units | `CurrentState.multiplierspeed/multiplieralt/multiplierdist`, `MainV2.ChangeUnits` | Unit conversion, no hard-coded units. MP converts **inside the CurrentState getters** with process-wide static multipliers: speed (groundspeed, airspeed, verticalspeed, targetairspeed) × {m/s 1, fps 3.28084, kph 3.6, mph 2.2369363, kts 1.9438444}; alt (alt, altasl, targetalt) and dist (wp_dist, DistToHome) × {m 1, ft 3.28084}. MP never converts HomeAlt (MP bug: ground band wrong in ft) or xtrack_error; turnrate uses the converted groundspeed. **Our design:** backend multipliers stay 1, so link B is always SI (link A must be: `spd_mps`, metres). The frontend converts for display only: `units.ts` (MP factors/labels), `kind` on bindings, card tiles, HUD tapes/texts. HomeAlt is converted with alt (fixes the MP bug); xtrack stays m. Operator picks units in the top bar (`UnitsSelect.svelte`, remembered per browser) | done |
| 11 | Loop pacing | `FlightData.mainloop` (50 ms loop; 40 / 75 / 300 ms, 3 s, 5 s timers) | Different work at different rates (MP's fast/slow split). MP runs one thread sleeping 50 ms per pass: CurrentState housekeeping, gated UI push (100 ms) and battery check every pass; AVI 40 ms, tuning graph 75 ms, log playback 300 ms, transponder 5 s (none ported); map marker + track every `FD_MapUpdateDelay` 0.3 s (2 s while disconnected, track capped at 200 points), auto-pan 3 s, mission overlay 5 s; ADS-B / avoidance markers hidden after 30 s / 10 s. **Frontend:** `pacing.ts` holds every cadence with its MP source: rAF frame clock, 100 ms UI gate, 250 ms wall clock, 1 s stats, link B 20 Hz / 2 Hz, STALE thresholds (moved from `config.ts`), and MP's map cadence for the map to come. Deviation: a Task 4 moving object older than 10 s is drawn STALE at its last known position, not hidden | done |

Do not port: GMap map, ZedGraph, `InitializeComponent` layout, video/AVI, 3D, speech, joystick,
ADS-B, no-fly zones, scripting. `FlightData` is 292 methods with ~5,400 WinForms references;
`MainV2.comPort` is a static global used 426 times in it.

Facts found while reading the IL:

- MP's HUD repaints when a bound value changes. Bound values reach the UI at most every 100 ms
  (`FlightData.updateBindingSource`), not every 50 ms as first assumed.

Backend requirements found in the IL (for when the vehicle backend is designed):

- **Call `cs.UpdateCurrentSettings(null, false, mav, mav.MAV)` regularly (MP: every mainloop pass).**
  Its housekeeping, gated to at most every 50 ms, does three things:
  - Sets `linkqualitygcs` = `packetsnotlost / (packetsnotlost + packetslost) * 100`, capped at 100,
    and sets it to **0 if no valid packet for > 10 s**.
  - Once per second: `distTraveled`, `timeInAir`, the wind estimate.
  - If the port is open, **re-requests every data stream**: EXTENDED_STATUS
    and POSITION at `ratestatus`/`rateposition`, EXTRA1/EXTRA2 at `rateattitude`, EXTRA3/RAW_SENSORS
    at `ratesensors`, RC_CHANNELS at `raterc`. **Corrected from the decompiled source:** this is not
    "after 8 s without data". The private `lastdata` is only set after a re-request
    (`lastdata = now + 30 s`), never when data arrives, so MP re-requests on the first call and then
    every 38 s. Our backend switches this off and uses `StreamRates` instead (logic 3).

  `~/Test_frontend` `Bridge.cs` never calls it, so the prototype's link quality never updates.
- **Send each vehicle's battery params to the frontend** (fetch: done, `BatteryParams.cs`) as a link B message `{ch:'params', vehicle,
  params:{BATT_LOW_VOLT, BATT_CRT_VOLT, BATT_LOW_MAH, BATT_CRT_MAH, BATT_CAPACITY}}`. Send it on
  connect and whenever they change. This needs a param fetch: MP reads `MAV.param`, and
  `Open(getparams:false)` skips the download, so fetch these five explicitly.
- **Forward every STATUSTEXT** (hook: done, `VehicleLink.StatusTextReceived`) as a link B message `{ch:'statustext', vehicle, t, severity, text}`
  (hook `OnPacketReceived` for msg 253: `cs.messages` has no severity). Never drop these: a slow
  client may coalesce SLOW snapshots, but status texts are a log. Keep MP's Settings `severity` at
  its default 4 so `messageHigh` selection matches the frontend's assumptions.
- **Never call `MainV2.ChangeUnits`-style code: keep `CurrentState.multiplierspeed/alt/dist` at 1.**
  They are static (process-wide), so any change would also corrupt the link A heartbeat to
  RoboCommand. Assert this at startup. Unit conversion is display-only, in the frontend.
- **Per-client push to the frontend must not queue.** (done: `LinkBHub.Client`) Like MP's pending-update skip, keep only the
  latest SLOW snapshot per vehicle per client, and drop older ones if the socket is slow. The
  frontend gate (`gate.ts`) protects the UI, but it cannot stop a backlog building in the socket.
- Core DLLs (`MissionPlanner.ArduPilot`, `Comms`, `MAVLink`, `Utilities`, `Interfaces`) target
  .NET Standard 2.0, so they can run on Mono or modern .NET. Their transitive dependencies are not
  yet checked on modern .NET.
- With `MAV_DATA_STREAM.ALL @ 4 Hz` the display can never exceed 4 real updates/s.

## Link B (built, backend side)

Messages, backend -> frontend, JSON text frames, `t` = backend Unix ms (`LinkBMessages.cs`):

| ch | When | Content |
|---|---|---|
| `att` | every ATTITUDE whose value changed | `r p y`, degrees, yaw 0..360 (CurrentState's conversion) |
| `status` | 2 Hz, only while the link is LIVE | `cs`: the `currentState.ts` fields by name; NaN/inf/unreadable left out |
| `params` | on connect, on change | the 5 `BATT_*` params |
| `statustext` | every autopilot STATUSTEXT | `t severity text` |
| `backend` | 1 Hz and on every link change | `links: {USV1: {state: closed/connecting/live/lost, error}}` |

Per client (`LinkBHub.Client`): latest-wins per vehicle for att/status/params, the STATUSTEXT log
queued in order and never coalesced (a client 5000 entries behind is disconnected). A new client
first gets the link states, params, last attitude and the last 1000 STATUSTEXTs per vehicle. A test
checks `SlowFields` against `currentState.ts`. Logic 9 added commands (`cmd` in; `cmdack`, `modes`
out; see the logic table), so `/linkb` checks `Origin` (`LinkBServer.OriginAllowed`): only this
server's own page or the Vite dev server (`localhost`/`127.0.0.1:5173`); a request without Origin
(not a browser page) is allowed. The per-client mailbox is `Mailbox.cs`.

Frontend side (`lib/linkB.ts`, real mode only): WebSocket to `/linkb` on the page's own origin; the
Vite dev server proxies `/linkb` to `127.0.0.1:5080`, so the URL is the same in dev and deployment.
Reconnects with backoff 0.5 s doubling to 5 s (`LINK_B_RECONNECT_MS`). Any message marks the
vehicle backend live. `backend` sets each vehicle's link (`vehicleLink` store): only LIVE counts as
connected, so a LOST vehicle is OFFLINE with the age of its last update about 1 s after its last
packet. The vehicle card says why ("Link LOST", "No connection: <error>", "Not configured on the
vehicle backend", "Vehicle backend not connected"). The socket closing makes every link B source
OFFLINE. Log entries use the backend time `t` (the history is replayed to new clients).

End-to-end check (2026-10-04, no hardware): a fake ArduPilot boat (MAVLink v1 on a Linux
pseudo-terminal, a scratch script) -> backend through MP's real `SerialPort` on .NET 10 -> page in
headless Chrome, both served by the backend and through the Vite proxy. Checked: LIVE data and HUD,
params, messages, LOST -> OFFLINE + "Link LOST", recovery, backend killed -> everything OFFLINE,
backend restarted -> page reconnects by itself. Note: MP takes `battery_voltage` from
BATTERY_STATUS (EXTRA3), not SYS_STATUS, so with EXTRA3 off the battery reads 0 V = CRITICAL.

## Map (built)

`components/MapPanel.svelte` (Leaflet 1.9.4 from npm, bundled: no CDN, no online tiles; a build
check found no tile URLs in the bundle) and `lib/mapModel.ts` (pure, tested with vitest:
`npm test`). Draws: the course boundary and the declared UAV geofence (link C), Task 4 keep-out
zones per vehicle type and the moving object (link C), vehicles with heading arrow (FAST yaw) and
track (link B; position only with a 3D fix, as for link A). Moving object: dead-reckoned from its
last MovingObjectAlert (position, heading, speed, OCS receive time) with its 10 m ring; after
`MAP_OBJECT_MAX_AGE_MS` (10 s) it is drawn grey, STALE, at its last reported position, not hidden.
A vehicle that is not LIVE is drawn grey at its last position with its state and age, and its track
stops. Safety banner on the map while a LIVE vehicle is inside a keep-out zone of its type, or 10 m
or closer to the (estimated) moving object if it affects that type. Cadence from `pacing.ts`: redraw
every 300 ms, 2 s while no vehicle is live; track 200 points; Follow (auto-pan every 3 s) per vehicle;
the view fits the course when a new course arrives.

Base layer (optional; the overlays work without it): `map/base.json` next to `index.html` (put it in
`frontend/public/map/`, copied to `dist/` by the build), either
`{"image": "map/course.png", "bounds": [[south, west], [north, east]], "opacity": 1}` (a georeferenced
image) or `{"tiles": "map/tiles/{z}/{x}/{y}.png", "maxZoom": 22}` (self-made tiles). Do not
bulk-download OSM tiles. Without it the map says "No base layer" and draws on a plain background.

End-to-end 2026-10-04: fake boat + backend + OCS real mode + simulator; test base image; KeepOutZone
and MovingObjectAlert published: zone, object (STALE after 10 s), boat and the "USV1 INSIDE keep-out
zone" banner all shown. Mock mode shows the same overlays.

## Task 4 replies (built, decided 2026-10-04)

`../task4.py`, same in real and local test mode. Chains (handbook + `Mission_details.pdf`):
AssistanceRequest -> IncidentAck -> ReadinessReport -> ReadinessConfirm; KeepOutZone -> IncidentAck;
AllClear -> IncidentAck; MovingObjectAlert: no ack. **Decided:** IncidentAcks automatic on receipt
("immediately"), from our vehicle of the command's domain (no vehicle of that domain, e.g. UUV: no
ack, console says so); the ReadinessReport only when the operator clicks **Report ready** in the
RoboCommand panel (confirmation dialog: the vehicle's distance to the point, its mode, warnings if
far, not in a hold/loiter mode, or not LIVE). Link C's one write action: `POST /task4/ready
{command_seq}` (Origin-checked, CORS preflight, 4 KB cap). Local test mode sends the ReadinessReport
3 s after the ack. The old `simulation/task4_responses.py` is no longer called: it acked
MovingObjectAlert and answered ReadinessConfirm with a ReadinessReport (both against the chains).
Task4State shows each item's ack, readiness and confirm. Tests: `../tests/test_task4.py`.
End-to-end 2026-10-04: real mode, commands 100/101/102/120 from a test publisher: 3 IncidentAcks
with the right command_seq, none for the MovingObjectAlert, browser click on Report ready ->
ReadinessReport command_seq 120, ReadinessConfirm for its report_seq -> "cleared to resume".
Not done: the vehicles do not act on Task 4 by themselves (no keep-out fence upload, no transit
to the assistance point): that is mission/autonomy integration.

## Link C (built)

OCS (`../main.py`) -> operator display, read-only Server-Sent Events, `../link_c.py` (standard
library), `http://127.0.0.1:5081/linkc` (`config.LINK_C_*`). Two messages, JSON in `data:` lines:
`state`, the whole OCS picture from `link_c_snapshot()` in `main.py` (MQTT connection and broker,
team, run: declared / declaration_seq / started / run_id, tiers, the 17 preflight items, command
counts and last command, last error, MQTT queue drops, course corners, the declared UAV geofence,
Task 4 state, link A per vehicle), every second and right after a change; and `log`, each
`[COMMAND]` / `[ERROR]` / `[VEHICLE...]` console line in order (the last 100 are replayed to a new
display). Task 4 state (`link_c.Task4State`): keep-out zones per vehicle type until an AllClear for
that type, the latest MovingObjectAlert (position, heading, speed, affected types, OCS receive time),
the latest AssistanceRequest and ReadinessConfirm; cleared at a new run_id. Never blocks the OCS:
OCS threads only append to memory; each display has its own thread, a slow one loses old log lines
(`log_dropped`) and is dropped after a 2 s blocked write. CORS: only `config.LINK_C_ALLOWED_ORIGINS`
(this display on :5080 and the Vite dev server); other origins get 403. `main.py` changes are
additive (hooks in `output`, `set_connection_state`, `set_preflight`, the Task 4 branch, RunStart,
the RunDeclaration geofence). Tests: `../tests/test_link_c.py`.

Frontend: `lib/linkC.ts` (EventSource on the page's host, port 5081; reconnects by itself),
`lib/ocs.ts` (types, `ocsState`, `ocsLog`), `components/RoboCommandPanel.svelte`: RoboCommand
connection, run, preflight (summary + checklist), heartbeats per vehicle, commands (counts, last,
log), Task 4 items with their age; NOT LIVE + dimmed when the feed is down. Mock mode feeds a mock
state (dev only). End-to-end 2026-10-04: OCS real mode + local broker + simulator + fake boat +
backend; KeepOutZone / MovingObjectAlert / AllClear published to the OCS showed and cleared; OCS
killed -> panel OFFLINE "NOT LIVE"; OCS restarted -> reconnected by itself, new run shown.

## Link A (built)

Vehicle backend -> OCS (`../main.py`), WebSocket `/linka` on the same Kestrel server, JSON
(`LinkA.cs`). `hb` per vehicle at 2 Hz, only while its link is LIVE; `backend` (link states) at 1 Hz
and on change. Latest-wins per client (`Mailbox.cs`, shared with link B).

`hb` = the RobotX `Heartbeat`: `type` (from the name, as `VEHICLE_TYPE`), `state` (`RobotState.cs`),
`lat lng` (only with a 3D fix: never 0,0), `spd_mps` (groundspeed), `heading_deg` (yaw 0..360),
`roll_deg`, `pitch_deg`; UAV only: `altitude_hae_m` and `flight_phase`. `altitude_hae_m` is
GPS_RAW_INT `alt_ellipsoid` (a MAVLink 2 extension; CurrentState has no ellipsoid height, and AMSL
differs from HAE by the geoid height) from the last 2 s with a 3D fix. Anything unknown is left out
and listed in `missing`. `depth_m` is for UUVs: never sent. Bench check: the UAV telemetry port must
speak MAVLink 2 (`SERIALn_PROTOCOL` = 2), or `altitude_hae_m` is always missing.

OCS side (`../vehicle_link.py`, standard library only, so the OCS dependencies do not change): a
minimal RFC 6455 client that reconnects (0.5 s doubling to 5 s). `main.py` (additive changes) starts
it at startup in real mode, and after the RunDeclaration publishes one heartbeat per fresh `hb`
(younger than `config.LINK_A_STALE_S` = 1 s, each used once, only while MQTT is connected). Operator
output: `[VEHICLE]` / `[VEHICLE LINK]` console lines on every change (stale, live, link state and
error, missing fields) and `vehicles: USV1=LIVE ...` in the 5 s status line. Local test mode is
unchanged (simulated telemetry). Tests: `OcsBackend.Tests/LinkATests.cs`, `../tests/test_vehicle_link.py`.

End-to-end check (2026-10-04, no hardware): two fake ArduPilot vehicles (boat sysid 1, quad sysid 2,
MAVLink 2 on pseudo-terminals) -> backend -> link A -> `main.py` real mode -> MQTT (a local amqtt
broker) -> RoboCommand simulator (RunDeclaration, RunStart) and a decoding subscriber. Checked: both
vehicles at 2 Hz with independent sequences; USV1 without altitude/phase; UAV1 `altitude_hae_m` 20.5
(the ellipsoid height, not the 12.0 m AMSL) and AIRBORNE; one vehicle silenced -> only its
heartbeats stop within 1 s, console STALE, resume on its own; backend killed -> all heartbeats stop,
OCS reconnects when it is back; local test mode still publishes the simulated heartbeats.

## Tech stack

### Decided (2026-10-04)

- **Vehicles:** Pixhawk running **ArduPilot** (USB ID `1209:5741`, "Generic Pixhawk1").
- **Vehicle link:** telemetry radio pair per vehicle: ground dongle on the OCS laptop, air module with
  antenna on the vehicle (serial MAVLink). Radio bandwidth is the budget for stream rates (logic 0 and 3).
- **Vehicle backend host:** **modern .NET (.NET 10 LTS)** loading the MP DLLs, not Mono.
  Load check passed 2026-10-04 (no hardware). The MP folder ships .NET Framework builds of
  `log4net.dll` and 30 `System.*`/`Microsoft.*` package DLLs. On .NET 10 these shadow the right
  assemblies (first failure: `System.Configuration.ConfigurationManager` without the type, via
  log4net in `CurrentState`'s static init). `OcsBackend.csproj` excludes them and uses packages
  instead: `log4net` 3.5.0 (netstandard; 2.0.17 has advisory GHSA-4f7c-pmjv-c25w) and
  `System.IO.Ports` 10.0.12. MP's `Newtonsoft.Json.dll` is a net45 build too (it throws
  `MissingMethodException` on `SecurityPermission` when it reflects; found when the test host
  crashed), so it is excluded and replaced by the `Newtonsoft.Json` 13.0.4 package (MP references
  assembly version 13.0.0.0). Add further packages only when a runtime load fails. Watch
  `System.Drawing.Common`: Windows-only on .NET 6+. .NET 8
  support ends Nov 2026. First task: a check that `MissionPlanner.ArduPilot.dll` and its
  dependencies load and connect on .NET 10 on Linux. Mono is the fallback only if that fails.
- **Three links:**

| Link | Flow | Rate | Transport |
|---|---|---|---|
| A | Vehicle backend -> OCS: real heartbeat data per vehicle (replaces the simulation in `main.py`) | 2 Hz, must be reliable | local WebSocket |
| B | Vehicle backend <-> frontend: attitude (fast), status (slow), commands back | ~20 Hz + 2 Hz | WebSocket |
| C | OCS -> frontend: RoboCommand connection, preflight, run ID, commands and responses | event-driven, ~1 Hz | read-only SSE |

  Rules: link C must never block the OCS (drop updates if the UI is slow or closed). If link A goes
  stale, the OCS must flag the vehicle to the operator, never publish old data as current.
  UAV heartbeats are relayed for Singapore Remote ID, so they must be complete and reliable.

- **Heartbeat field sources** (`CurrentState`): position `lat/lng`; speed `groundspeed`; heading
  `yaw` or `groundcourse`; `roll`, `pitch`, `alt`; robot state derived from `armed` + `mode` (killed
  depends on the e-stop wiring); UAV flight phase derived from mode + landed state. `current_task` is
  not in `CurrentState`.

- **Autonomy:** UAV gets a companion computer running OpenCV (needed for Task 3). USV has no
  companion computer.

- **Frontend (decided 2026-10-04):**
  - **Svelte 5 + TypeScript + Vite**. The build output is static files with no CDN. The HUD is drawn on a
    plain canvas with a `requestAnimationFrame` loop, outside Svelte's reactivity.
  - **Map:** Leaflet with a **local** base layer: our own georeferenced image of the course, or
    self-generated tiles. Do not bulk-download from OSM's tile servers (that breaks their policy). The
    map must still work with no base layer, because the overlays (vehicles, keep-out areas, moving
    object, 10 m ring) are what matter.
  - **Packaging:** an ordinary web page served as static files by the .NET backend (Kestrel).
    Development happens in any browser. For runs, a launch script opens it in kiosk mode (Chromium
    `--app/--kiosk` or `firefox --kiosk`) so the operator can't close it or push it into a
    background tab, where `requestAnimationFrame` gets throttled. `main.py`'s SSE (link C) will need a
    CORS header, which is an additive change.
  - **Mock mode:** dev only (`npm run dev:mock`, Vite `--mode mock`). It shows a large MOCK DATA
    banner. A production build (`npm run build`) is always real mode, so mock can never run in a
    real deployment.
- **.NET 10 check:** deferred. It is test-bench work and runs later. The frontend shell is built first.

- **Vehicle card (operator focus, 2026-10-04):** each vehicle card shows only what the competition
  needs, in this order:
  1. RobotX robot state (AUTO/MANUAL/KILLED/UNKNOWN) and mode. For the UAV, also the flight phase.
  2. Current task.
  3. Alerts, shown only when something is active.
  4. Six key tiles: battery, GPS, link, speed, heading, and altitude (UAV) or distance to waypoint (USV).
  5. The message line.

  The full binding table sits behind a collapsed "All telemetry" section. The derivation lives in
  `frontend/src/lib/autonomy.ts`: AUTO when armed and the mode is in the per-type autonomous list,
  MANUAL when armed otherwise, flight phase from `landed_state`. These rules are **provisional:
  confirm with the team**. Link A uses the same rules (`backend/OcsBackend/RobotState.cs`; a test
  compares its mode lists with `autonomy.ts`). Modes are compared upper-case, letters and digits
  only, because MP's names come from the parameter metadata and differ: Rover `SmartRTL`, Copter
  `Smart_RTL` and `Auto RTL` (fixed 2026-10-04: SmartRTL/Auto RTL were read as MANUAL). Rover
  `Dock`/`Circle` are not in MP's metadata, so MP cannot name them: they would read as MANUAL.
  KILLED needs the e-stop signal.

### Open

- Confirm the robot-state rules in `autonomy.ts` (autonomous mode lists per vehicle type; what
  "killed" means for each vehicle's e-stop).

- Who sets `current_task` and task reports for the USV (no companion computer): OCS operator input?
  Until decided, link A heartbeats send TASK_NONE (`main.py` `vehicle_current_task`).
- Link A decision to confirm: when a vehicle's data stops, the OCS stops that vehicle's heartbeats
  (and tells the operator) rather than publishing STATE_UNKNOWN heartbeats without telemetry.
- How UAV OpenCV results reach the OCS: over the same telemetry radio as MAVLink, or a separate link.
- Telemetry radio air data rate (sets the stream-rate budget).

## Future: a different HUD per vehicle (planned, not now)

The user has a plan for a different HUD, probably a USV-specific one that replaces the aircraft-style
MP HUD. **Do not build it until the user asks.** Today both vehicles use the MP port (`hudDraw.ts`).
For the USV it only switches off the altitude tape and the airspeed line.

The HUD is designed to be unplugged and replaced. A new renderer is a pure function with the same
signature, so nothing upstream of it changes.

```
attitude.ts (FAST, per frame) ─┐
telemetry.ts (SLOW, gated)  ───┼─> Hud.svelte builds HudInput ─> drawHud(g, W, H, input, options)
frameClock.ts (rAF)         ───┘        └─ then draws the NOT LIVE / STALE overlay on top
```

Files, all under `frontend/src/`:

| File | Role | Change for a new HUD? |
|---|---|---|
| `lib/hudDraw.ts` | MP HUD renderer: `drawHud()`, `HudInput`, `HudOptions` | Keep it as the UAV renderer. Add a new file (e.g. `lib/hudDrawUsv.ts`) with the same signature |
| `components/Hud.svelte` | Builds `HudInput` from the attitude track + SLOW snapshot, calls the renderer (`draw()`, the `drawHud(...)` call and the `showAlt`/`showAirspeed` options), and draws the NOT LIVE overlay | **Swap point:** choose the renderer by `VEHICLE_TYPE[vehicle]`. Extend `HudInput` here if the new HUD needs more fields |
| `lib/config.ts` | `VEHICLE_TYPE` (USV1 → USV, UAV1 → UAV) | Only if vehicles or types change |
| `lib/currentState.ts`, `lib/bindings.ts` | SLOW fields from `CurrentState` | Only if the new HUD needs a field not sent yet. Add it to both, and to the backend serializer |
| `components/VehicleStatus.svelte` | Vehicle card. Per-type tiles at the `{#if type === 'UAV'}` blocks | Only if the card should change with the HUD |
| `App.svelte` | HUD panel + USV1/UAV1 tabs | Only if the layout changes (e.g. two HUDs side by side) |

Do not change these when swapping the HUD. They hold safety and timing behaviour that every
renderer relies on:

- `lib/frameClock.ts`: one rAF loop.
- `lib/attitude.ts`: extrapolation and ease (logic 0).
- `lib/gate.ts`: UI update gate (logic 2).
- `lib/source.ts` and `lib/sources.ts`: LIVE/STALE/OFFLINE.
- The NOT LIVE overlay in `Hud.svelte`. Every renderer must stay under it, so a frozen display is
  never read as a still vehicle.
