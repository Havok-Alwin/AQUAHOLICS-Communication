# AQUAHOLICS RobotX 2026 OCS

Operator Control Station communication implementation for RobotX 2026.

## Current implementation

- MQTT connection to RoboCommand
- RxCourse reception and protobuf decoding
- Course validation
- RunDeclaration
- USV1 and UAV1 heartbeats
- STATE_AUTO reporting
- RunStart handling
- declaration_seq verification
- run_id storage
- Independent vehicle sequence numbers
- PipelineSurveyReport
- ResourceDeliveryRequest
- DockingReport
- FirefightingReport
- Logging
- MQTT reconnect/subscription restoration
- Task 4 command handlers

## Source files

- ocs/main.py - main OCS application and RoboCommand communication flow
- ocs/config.py - team, MQTT and local-test configuration
- ocs/sequence_manager.py - RxRequest and per-vehicle RxReport sequence handling
- ocs/task_reports.py - RobotX task report generation
- ocs/logger.py - OCS session logging

## Important

This project uses the official RobotX 2026 protobuf schemas and generated Python classes from RoboNation's RoboCommand repository.

Current telemetry/task values are simulated for local protocol testing. Real USV/UAV integration is pending.
