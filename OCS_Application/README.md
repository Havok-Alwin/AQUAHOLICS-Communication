# AQUAHOLICS RobotX 2026 OCS

The OCS application communicates with RoboCommand over MQTT using the RobotX
protobuf messages. This folder contains the OCS client; RoboCommand runs
separately (for example, on another laptop).

## Implemented workflow

- Waits for DHCP-provided network connectivity to the configured broker, then
  connects to MQTT. It retries while the broker or network is unavailable.
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

## Network setup

Set `ROBOTX_BROKER` on the OCS laptop to the RoboCommand laptop's reachable
hostname or DHCP-assigned IP address. Set `ROBOTX_PORT` if the broker uses a
port other than `1883`. DHCP must provide the OCS laptop with an address and a
route to the broker; the OCS checks network readiness but does not configure
DHCP. `localhost` is the default broker address for local testing.

For a real network run, disable local simulation with
`ROBOTX_LOCAL_TEST=0`. The default is local test mode, which can publish
simulated heartbeats and automatic task reports; do not use that mode with
competition vehicles.

## Source files

- `main.py` — MQTT connection, receive processing, run declaration, heartbeats,
  command handling, and operator output.
- `config.py` — team and vehicle IDs, broker settings, topics, and local test
  settings.
- `sequence_manager.py` — request and per-vehicle report sequence counters.
- `task_reports.py` — task report message builders and MQTT publishing.
- `logger.py` — best-effort session logging.
- `../Robocmd_Application/gen/python/` — generated protobuf classes used by the
  OCS.
