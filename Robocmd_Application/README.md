# RoboCommand Application — two-laptop OCS test

This folder runs a small RoboCommand simulator on one laptop. It supplies the
MQTT side of the RobotX communications path so a second laptop can run the
team OCS application.

```text
OCS laptop                          RoboCommand laptop
----------                          -------------------
OCS application  <-- MQTT :1883 --> Mosquitto broker
      ^                                     ^
      |                                     |
      +------ DHCP address + gateway -------+
```

The simulator publishes a retained `RxCourse`, receives the OCS
`RunDeclaration`, then sends a matching `RunStart`. It also receives OCS
heartbeats/task reports and checks their topic and sequence information.

## Folder contents

| Path | Purpose |
| --- | --- |
| `rc_test/robocommand_simulator.py` | The RoboCommand MQTT simulator and interactive command console. |
| `rc_test/requirements.txt` | Python dependencies: `paho-mqtt` and `protobuf`. |
| `docker-compose.broker.yml` | Starts only the Mosquitto MQTT broker. Use this file. |
| `mosquitto/mosquitto.conf` | Broker configuration: anonymous MQTT on TCP port 1883. |
| `gen/python/` | Generated protobuf classes used by the simulator. Do not edit these manually. |
| `docker-compose.yml` | Older project Compose file. Do not use it for this standalone simulator. |

## Run on the RoboCommand laptop

Install Docker, the Compose plugin, Python, and pip. Then, from this folder:

```bash
python3 -m pip install -r rc_test/requirements.txt
docker compose -f docker-compose.broker.yml up -d
python3 rc_test/robocommand_simulator.py --interactive
```

The simulator connects to the broker at `127.0.0.1:1883`. Docker exposes that
broker on every network interface of the RoboCommand laptop, including its
Ethernet interface, unless the laptop firewall blocks TCP port 1883.

Stop the broker when finished:

```bash
docker compose -f docker-compose.broker.yml down
```

## Interactive test commands

At the `RoboCommand>` prompt:

```text
course alpha
course bravo
course charlie
course delta
```

Each command publishes a new retained `RxCourse` with a distinct test course
ID, boundary, and pinger frequency. The coordinates are synthetic test data,
not competition geometry.

```text
status
start RMKE
assistance RMKE usv
malformed-course
help
quit
```

`status` shows declarations, active runs, and last report sequence per vehicle.
`assistance` sends a Task 4 `AssistanceRequest` after a run is active.
`malformed-course` publishes invalid bytes to confirm that the OCS logs a
protobuf parse error and stays connected.

## Expected message flow

1. Start Mosquitto and the simulator. The simulator publishes retained
   `robocommand/robotx/course`.
2. The OCS gets its Ethernet configuration by DHCP and discovers the
   RoboCommand laptop as its default gateway.
3. The OCS connects to `<gateway IP>:1883`, subscribes to the course and its
   team command topic, then receives the retained course immediately.
4. The OCS sends `robocommand/robotx/<team_id>/request` containing
   `RunDeclaration`.
5. The simulator sends `robocommand/robotx/<team_id>/command` containing a
   matching `RunStart`.
6. The OCS may send heartbeat and task reports to
   `robocommand/robotx/<team_id>/<vehicle_id>/report`.

For the current OCS skeleton, run it with `ROBOTX_VEHICLE_IDS=USV1` unless you
also configure its required closed UAV geofence. Its telemetry hook currently
returns no real telemetry, so no heartbeat will be emitted until that hook is
connected to a vehicle source.

## DHCP test requirement

This Python program is RoboCommand's MQTT application. It does **not** run a
DHCP server because DHCP needs administrator access and must attach to the
specific physical Ethernet interface.

For a course-style test, configure the RoboCommand laptop's Ethernet interface
with its course address and run a DHCP service such as `dnsmasq` on that
interface. The DHCP lease must advertise the RoboCommand laptop's Ethernet IP
as the default gateway. The OCS Ethernet interface must remain a DHCP client;
do not set a static OCS course address.

| Course-style test | RoboCommand Ethernet IP | DHCP subnet |
| --- | --- | --- |
| Alpha | `192.168.65.2` | `192.168.65.0/24` |
| Bravo | `192.168.66.2` | `192.168.66.0/24` |
| Charlie | `192.168.67.2` | `192.168.67.0/24` |
| Delta | `192.168.68.2` | `192.168.68.0/24` |

After connecting the Ethernet cable, verify on the OCS laptop:

```bash
ip -4 addr
ip -4 route show default
```

The default route should point to the selected RoboCommand Ethernet IP. Then
start the OCS without `ROBOTX_BROKER`; it should discover that gateway and
connect automatically.

## Quick debugging map

| Symptom | Check |
| --- | --- |
| Simulator retries connection | Confirm `docker compose -f docker-compose.broker.yml up -d` is running. |
| OCS cannot connect | Confirm the OCS gateway equals the RoboCommand Ethernet IP, TCP 1883 is open, and Docker publishes port 1883. |
| OCS gets no course | Verify it subscribed to `robocommand/robotx/course`; restart the OCS to receive the retained message again. |
| No RunStart | Check the simulator terminal for a valid `RunDeclaration` from the expected team. |
| OCS rejects its own declaration | Check team ID, configured vehicle IDs, task tiers, and UAV geofence requirements. |
| Reports rejected | Ensure the MQTT topic team/vehicle names exactly match the `RxReport` envelope and report sequence increments per vehicle. |
