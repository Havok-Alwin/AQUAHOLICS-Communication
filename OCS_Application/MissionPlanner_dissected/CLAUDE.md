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
  There is no connect loop yet (logic 8), so nothing calls `StreamRates.Tick()` outside the tests.
- `frontend/`: Svelte 5 + TS + Vite. The shell (layout, source/staleness model, mock-mode banner) and
  all frontend logic items (0, 1, 2, 5, 6, 7, 10, 11) are done. There is no link B/C transport yet.
  In mock mode, `src/lib/mock.ts` feeds link B messages, and that file is excluded from production
  builds.
- Tech stack: frontend decided. See the "Tech stack" section below.

## Next step

Review logic 3. All frontend logic items are done. Remaining work:
- Frontend, not in the logic table: the map (Leaflet, local base layer, Task 4 keep-out areas and the
  moving object, using the cadence in `pacing.ts`); the RoboCommand panel (link C); the transport
  clients (link B WebSocket, link C SSE) that call `handleLinkB` and set the Sources connected.
- Backend logic items, built with the backend: 8 (connect/link-lost; it calls
  `StreamRates.Tick()` and `cs.UpdateCurrentSettings` from its loop), 9 (commands). See "Backend
  requirements found in the IL".
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
| 8 | Connect / link-lost | `MainV2` connect flow | Open link, request streams, detect lost heartbeat so a frozen display is not read as a still vehicle | todo |
| 9 | Commands | `MAVLinkInterface.doARM`, `setMode`, `doCommand` | Arm/disarm, mode change; mode lists differ per vehicle type | todo |
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
- **Send each vehicle's battery params to the frontend** as a link B message `{ch:'params', vehicle,
  params:{BATT_LOW_VOLT, BATT_CRT_VOLT, BATT_LOW_MAH, BATT_CRT_MAH, BATT_CAPACITY}}`. Send it on
  connect and whenever they change. This needs a param fetch: MP reads `MAV.param`, and
  `Open(getparams:false)` skips the download, so fetch these five explicitly.
- **Forward every STATUSTEXT** as a link B message `{ch:'statustext', vehicle, t, severity, text}`
  (hook `OnPacketReceived` for msg 253: `cs.messages` has no severity). Never drop these: a slow
  client may coalesce SLOW snapshots, but status texts are a log. Keep MP's Settings `severity` at
  its default 4 so `messageHigh` selection matches the frontend's assumptions.
- **Never call `MainV2.ChangeUnits`-style code: keep `CurrentState.multiplierspeed/alt/dist` at 1.**
  They are static (process-wide), so any change would also corrupt the link A heartbeat to
  RoboCommand. Assert this at startup. Unit conversion is display-only, in the frontend.
- **Per-client push to the frontend must not queue.** Like MP's pending-update skip, keep only the
  latest SLOW snapshot per vehicle per client, and drop older ones if the socket is slow. The
  frontend gate (`gate.ts`) protects the UI, but it cannot stop a backlog building in the socket.
- Core DLLs (`MissionPlanner.ArduPilot`, `Comms`, `MAVLink`, `Utilities`, `Interfaces`) target
  .NET Standard 2.0, so they can run on Mono or modern .NET. Their transitive dependencies are not
  yet checked on modern .NET.
- With `MAV_DATA_STREAM.ALL @ 4 Hz` the display can never exceed 4 real updates/s.

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
  confirm with the team**. Link A must use the same rules. KILLED needs the e-stop signal.

### Open

- Confirm the robot-state rules in `autonomy.ts` (autonomous mode lists per vehicle type; what
  "killed" means for each vehicle's e-stop).

- Who sets `current_task` and task reports for the USV (no companion computer): OCS operator input?
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
