# AQUAHOLICS RobotX 2026 OCS

The OCS application communicates with RoboCommand over MQTT using the RobotX
protobuf messages. This folder contains the OCS client; RoboCommand runs
separately (for example, on another laptop).

## Implemented workflow

- Finds the DHCP-provided IPv4 default gateway when `ROBOTX_BROKER` is unset,
  checks the route and the course network (subnet, gateway), then connects to MQTT. It retries while network or broker
  connectivity is unavailable.
- Restores the course and team command subscriptions after reconnecting.
- Receives and validates `RxCourse` messages, then sends a `RunDeclaration`.
- Publishes USV1 and UAV1 heartbeat reports with independent per-vehicle
  sequence numbers. Task report publishers use those same per-vehicle counters.
- Receives `RunStart`, checks its declaration sequence, and stores its run ID.
- Handles supported Task 4 commands, rejects malformed or unexpected command
  payloads, checks team and vehicle identities, and reports response status.
- Ignores retained commands and discards queued messages from a disconnected
  MQTT connection to reduce the chance of reusing stale messages.
- Logs connection state, subscriptions, received commands, publications,
  sequence numbers, and parsing/validation errors. Console status remains
  available if file logging fails.

## Still pending

- Real USV/UAV telemetry and vehicle command adapters. Current telemetry and
  local task responses are simulated for protocol testing.
- Passing validated course data into the navigation/vehicle control system.
- An operator dashboard; connection and command status are currently shown in
  the console and session log.
- Integration with the separately running RoboCommand application on the
  other laptop.

## Running the OCS

The default is **real mode**: no simulated telemetry, task reports or Task 4
responses, and no `localhost` fallback. Do not enable simulation with
competition vehicles.

```bash
python3 main.py --team-id <ID> --subnet 192.168.10.0/24 --robocommand-ip 192.168.10.1 \
                --task1-tier core --task2-tier disruptive \
                --task3-tier disruptive --task4-tier disruptive
```

### Command-line flags (per-event values)

| Flag | Environment variable | Meaning |
|---|---|---|
| `--team-id ID` | `ROBOTX_TEAM_ID` | Assigned team ID (default in `config.py`) |
| `--task1-tier` .. `--task4-tier` | `ROBOTX_TASK1_TIER` .. `ROBOTX_TASK4_TIER` | `none`, `core`, `advanced` or `disruptive` |
| `--course-id ID` | `ROBOTX_COURSE_ID` | Expected course ID; a different received `course_id` is rejected |
| `--uav-geofence FILE` | `ROBOTX_UAV_GEOFENCE` | JSON file `[[lat, lon], ...]`, closed ring; required in real mode |
| `--subnet CIDR` | `ROBOTX_SUBNET` | Expected course subnet |
| `--robocommand-ip IP` | `ROBOTX_ROBOCOMMAND_IP` | Expected RoboCommand IP (the default gateway) |

Flags override environment variables. The team ID and tiers are printed and
logged at startup so the operator can confirm them. `python3 main.py --help`
lists the flags.

### Environment-only options

| Variable | Default | Meaning |
|---|---|---|
| `ROBOTX_LOCAL_TEST` | `0` | `1` enables local simulation (fake telemetry, reports, Task 4 responses) and the `localhost` fallback |
| `ROBOTX_AUTO_REPORT_TEST` | `1` | With local test on, `0` disables automatic fake task reports |
| `ROBOTX_AUTO_TASK4_RESPONSES` | `1` | With local test on, `0` disables automatic fake Task 4 responses |
| `ROBOTX_BROKER` | DHCP gateway | Broker hostname or IP override |
| `ROBOTX_PORT` | `1883` | Broker port |
| `ROBOTX_NETWORK_STRICT` | `1` | `0` turns network-check failures into warnings |

Example, against the Robocmd simulator on the same machine:

```bash
ROBOTX_LOCAL_TEST=1 python3 main.py                 # simulated data
ROBOTX_BROKER=127.0.0.1 python3 main.py             # real mode, local broker
```

### Course validation

A received `RxCourse` is rejected unless: `course_id` matches `--course-id`
(skipped with a warning if not given); the boundary has at least 3 distinct
corners with valid lat/lon; the boundary has non-zero area and does not
self-intersect (an explicitly closed ring that repeats its first point is
accepted); and the timestamp is present. A validated course is stored
(`get_validated_course()` in `main.py`) for later steps.

### UAV geofence

Real mode needs `--uav-geofence uav_geofence.json`, a JSON list of
`[lat, lon]` points with the first point repeated as the last. Before the
`RunDeclaration` is sent it must be closed, have at least 3 distinct points
with non-zero area and no self-intersection, lie entirely inside the received
course boundary (points on the edge count as inside, and no geofence edge may
cross the boundary). If any check fails, or no geofence is configured, the
`RunDeclaration` is blocked with an error and retried when the course is received
again. In local test mode with no file, a test geofence is derived from the course
(shrunk to 50% about its centre) so the simulator courses work.

### Schema and vehicle ID checks

At startup the OCS hashes the generated protobuf files
(`../Robocmd_Application/gen/python`) and compares the SHA-256 with
`APPROVED_SCHEMA_HASH` in `config.py`; the hash and protobuf runtime version
are printed and logged. A mismatch blocks startup in real mode (warning in
local test mode). Print the current hash with `python3 startup_checks.py`, and
update `APPROVED_SCHEMA_HASH` when a new schema release is approved
(`ROBOTX_SCHEMA_HASH` overrides it). The vehicle IDs must be non-empty, unique,
free of whitespace and MQTT wildcard characters, equal to `[USV_ID, UAV_ID]`,
and round-trip through their report topic; `RunDeclaration` and heartbeats
both use the single `VEHICLE_IDS` list. A failure stops the OCS.

## Network setup

By default the OCS discovers the DHCP-provided IPv4 default gateway and uses it
as the RoboCommand broker address. In the two-laptop setup, configure DHCP so
the RoboCommand laptop is the OCS laptop's default gateway. DHCP must provide
the OCS laptop with an address and route; the OCS does not configure DHCP. With
no gateway, the OCS keeps waiting and retrying (only local test mode falls back
to `localhost`).

### Network check

Before each connection attempt the OCS prints and logs its interface, IP,
gateway and broker, and checks that:

- the local IP is not link-local (169.254.x.x, meaning no DHCP lease);
- the local IP is inside `--subnet`;
- the default gateway and the broker equal `--robocommand-ip`.

In real mode a failed check blocks the connection and is retried every 2
seconds (`ROBOTX_NETWORK_STRICT=0` only warns). If the subnet or RoboCommand IP
is not given, those two checks are skipped with a warning. Loopback brokers skip
the course checks. **Manual checks:** the interface is set to DHCP, network
bridging is off, and Internet sharing is off.

## Source files

- `main.py` — MQTT connection, receive processing, run declaration, heartbeats,
  command handling, and operator output.
- `config.py` — team and vehicle IDs, task tiers, broker and network-check
  settings, topics, and local test settings.
- `sequence_manager.py` — request and per-vehicle report sequence counters.
- `task_reports.py` — task report message builders and MQTT publishing.
- `startup_checks.py` — protobuf schema hash and vehicle ID checks.
- `logger.py` — best-effort session logging.
- `../Robocmd_Application/gen/python/` — generated protobuf classes used by the
  OCS; the application adds this directory to its Python import path.
