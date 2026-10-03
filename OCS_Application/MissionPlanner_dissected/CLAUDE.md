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

- `backend/`: MP DLLs plus `Bridge.cs`. **`Bridge.cs` is a throwaway test, not the base.** The real
  backend server is designed after the tech stack is chosen.
- `frontend/`: phase 1 (shell) built. Svelte 5 + TS + Vite. It has the layout, a source/staleness model
  and the mock-mode banner. No transport and no logic items yet.
- Tech stack: frontend decided. See the "Tech stack" section below.

## Next step

Review the frontend shell. Then the logic items one at a time. The .NET 10 check (a small console app
that loads `backend/MissionPlanner.ArduPilot.dll`, connects to the Pixhawk on USB and prints
roll/pitch/yaw) is deferred to test-bench time. Mono is the fallback only if it fails.

## Reference material (outside this folder)

- `~/MissionPlanner-latest/`: the full Mission Planner build (binaries only, no C# source). Read its
  logic from the IL: `ikdasm ~/MissionPlanner-latest/MissionPlanner.exe > mp.il` (FlightData,
  MainV2), `MissionPlanner.Controls.dll` (HUD), `MissionPlanner.ArduPilot.dll` (CurrentState,
  MAVLinkInterface). Upstream source: github.com/ArduPilot/MissionPlanner.
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
| 0 | Rendering pipeline (our own, tested in `~/Test_frontend`) | not MP | Per-stream rates instead of `ALL @ 4 Hz`; fast channel (roll/pitch/yaw, pushed on change, ~20 Hz) vs slow channel (status, 2 Hz); draw on a fixed frame clock (`requestAnimationFrame`), extrapolate with velocity from the last two samples, light 15 ms ease. Simulation at 20 Hz: RMS error 1.4 deg -> 0.7 deg, biggest frame jump 4.7 -> 1.8 deg. At 4 Hz no smoothing helps, so the data rate is the real fix | todo |
| 1 | Binding map | `FlightData.InitializeComponent` (72 `Binding`s) | HUD field <- `CurrentState` field, e.g. heading<-yaw, status<-armed, message<-messageHigh, gpsfix<-gpsstatus, batterylevel<-battery_voltage, navroll<-nav_roll, targetheading<-nav_bearing, disttowp<-wp_dist, groundalt<-HomeAlt, plus ekfstatus, prearmstatus, failsafe, linkqualitygcs, vibex/y/z | todo |
| 2 | Update gate | `CurrentState.UpdateCurrentSettings` | Pushes to UI at most every 50 ms (20 Hz) | todo |
| 3 | Stream-rate setup | `MAVLinkInterface.requestDatastream`; `cs.rateattitude/rateposition/ratestatus/ratesensors/raterc` (defaults 4/2/2/2) | Requests each MAVLink message group at its own rate | todo |
| 4 | Invalidate on change | `HUD.set_roll` -> `Invalidate()` | MP's HUD is event-driven, not timer-driven (item 0 improves on this) | todo |
| 5 | HUD geometry | `HUD.doPaint` | Pitch-ladder px/deg, ticks, heading tape, aircraft symbol, colours | todo |
| 6 | Warning thresholds | `HUD.lowgroundspeed/lowairspeed/lowvoltagealert/criticalvoltagealert/failsafe/safetyactive` | When to alert the operator. Safety-relevant: port exactly | todo |
| 7 | Status text severity | `cs.messageHigh`, `cs.messageHighSeverity` (`MAV_SEVERITY`) | Colour/priority of vehicle messages | todo |
| 8 | Connect / link-lost | `MainV2` connect flow | Open link, request streams, detect lost heartbeat so a frozen display is not read as a still vehicle | todo |
| 9 | Commands | `MAVLinkInterface.doARM`, `setMode`, `doCommand` | Arm/disarm, mode change; mode lists differ per vehicle type | todo |
| 10 | Units | `CurrentState.multiplierspeed/multiplieralt/AltUnit` | Unit conversion, no hard-coded units | todo |
| 11 | Loop pacing | `FlightData.mainloop` (40 / 75 / 300 ms timers) | Different work at different rates (MP's fast/slow split) | todo |

Do not port: GMap map, ZedGraph, `InitializeComponent` layout, video/AVI, 3D, speech, joystick,
ADS-B, no-fly zones, scripting. `FlightData` is 292 methods with ~5,400 WinForms references;
`MainV2.comPort` is a static global used 426 times in it.

Facts found while reading the IL:

- MP's HUD repaints when a bound value changes; values update at most every 50 ms.
- Core DLLs (`MissionPlanner.ArduPilot`, `Comms`, `MAVLink`, `Utilities`, `Interfaces`) target
  .NET Standard 2.0, so they can run on Mono or modern .NET. Their transitive dependencies are not
  yet checked on modern .NET.
- With `MAV_DATA_STREAM.ALL @ 4 Hz` the display can never exceed 4 real updates/s.

## Tech stack

### Decided (2026-10-04)

- **Vehicles:** Pixhawk running **ArduPilot** (USB ID `1209:5741`, "Generic Pixhawk1").
- **Vehicle link:** telemetry radio pair per vehicle: ground dongle on the OCS laptop, air module with
  antenna on the vehicle (serial MAVLink). Radio bandwidth is the budget for stream rates (logic 0 and 3).
- **Vehicle backend host:** **modern .NET (.NET 10 LTS)** loading the MP DLLs, not Mono. .NET 8
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

### Open

- Who sets `current_task` and task reports for the USV (no companion computer): OCS operator input?
- How UAV OpenCV results reach the OCS: over the same telemetry radio as MAVLink, or a separate link.
- Telemetry radio air data rate (sets the stream-rate budget).
