# AQUAHOLICS ROBOTX 2026 OCS STARTUP CHECKS
#
# Protobuf schema fingerprint and vehicle ID consistency.
# Print the current schema hash with:  python3 startup_checks.py

import hashlib
import os

import config

GEN_PYTHON_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Robocmd_Application",
    "gen",
    "python",
)


def schema_hash(root=GEN_PYTHON_PATH):
    """SHA-256 over every generated .py file (relative path + content).

    Line endings are normalised so the hash is the same on every checkout.
    """
    digest = hashlib.sha256()
    count = 0
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, "rb") as handle:
                content = handle.read().replace(b"\r\n", b"\n")
            digest.update(relative.encode("utf-8") + b"\0" + content + b"\0")
            count += 1
    return (digest.hexdigest(), count) if count else (None, 0)


def protobuf_runtime_version():
    try:
        import google.protobuf
        return google.protobuf.__version__
    except Exception:
        return "unknown"


def check_schema():
    """Return (info, problems). problems are blocking in real mode."""
    actual, count = schema_hash()
    info = [f"protobuf schema: {count} files, sha256={actual}",
            f"protobuf runtime: {protobuf_runtime_version()}"]
    problems = []
    if actual is None:
        problems.append(f"no generated protobuf files found in {GEN_PYTHON_PATH}")
    elif not config.APPROVED_SCHEMA_HASH:
        info.append("WARNING: approved schema hash not configured; comparison skipped")
    elif actual != config.APPROVED_SCHEMA_HASH.lower():
        problems.append("protobuf schema does not match the approved release "
                        f"(expected {config.APPROVED_SCHEMA_HASH}, got {actual})")
    return info, problems


def check_vehicle_ids():
    """Return a list of problems with the configured vehicle IDs."""
    problems = []
    ids = list(config.VEHICLE_IDS)
    if not ids:
        problems.append("no vehicle IDs configured")
    for vid in ids:
        if not isinstance(vid, str) or not vid.strip():
            problems.append(f"empty vehicle ID {vid!r}")
        elif vid != vid.strip() or any(ch in vid for ch in "/+#") or any(ch.isspace() for ch in vid):
            problems.append(f"vehicle ID {vid!r} contains whitespace or MQTT topic characters")
    if len(set(ids)) != len(ids):
        problems.append(f"vehicle IDs are not unique: {ids}")
    if ids != [config.USV_ID, config.UAV_ID]:
        problems.append(f"VEHICLE_IDS {ids} != [USV_ID, UAV_ID] {[config.USV_ID, config.UAV_ID]}")
    for vid in ids:
        parts = config.report_topic(vid).split("/")
        if len(parts) != 5 or parts[2] != config.TEAM_ID or parts[3] != vid or parts[4] != "report":
            problems.append(f"report topic for {vid!r} does not round-trip: {config.report_topic(vid)}")
    return problems


def check_sequences():
    """Prove report sequence counters are independent per vehicle, then reset them."""
    import sequence_manager
    problems = []
    ids = list(config.VEHICLE_IDS)
    sequence_manager.reset_sequences()
    try:
        for index, vid in enumerate(ids, start=1):
            for _ in range(index):
                sequence_manager.next_report_sequence(vid)
        for index, vid in enumerate(ids, start=1):
            if sequence_manager.get_report_sequence(vid) != index:
                problems.append(f"report sequence for {vid!r} is not independent")
    finally:
        sequence_manager.reset_sequences()
    return problems


if __name__ == "__main__":
    value, files = schema_hash()
    print(f"{value}  ({files} files in {GEN_PYTHON_PATH})")
