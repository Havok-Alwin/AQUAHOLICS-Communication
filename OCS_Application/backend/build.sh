#!/bin/sh
cd "$(dirname "$0")"
N=/usr/lib/mono/4.5/Facades/netstandard.dll
mcs -out:Bridge.exe -r:$N -r:MissionPlanner.ArduPilot.dll -r:MissionPlanner.Comms.dll -r:MAVLink.dll -r:Interfaces.dll -r:MissionPlanner.Utilities.dll Bridge.cs
