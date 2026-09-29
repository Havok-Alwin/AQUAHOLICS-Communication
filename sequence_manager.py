# ============================================================
# AQUAHOLICS ROBOTX 2026
# SEQUENCE MANAGER
# ============================================================

import threading


_lock = threading.Lock()


# ============================================================
# RxRequest SEQUENCE
#
# Team-level requests:
# RunDeclaration etc.
# ============================================================

_request_sequence = 0


def next_request_sequence():

    global _request_sequence

    with _lock:

        _request_sequence += 1

        return _request_sequence


# ============================================================
# RxReport SEQUENCES
#
# Each vehicle has its own independent sequence stream.
# ============================================================

_report_sequences = {}


def next_report_sequence(vehicle_id):

    with _lock:

        if vehicle_id not in _report_sequences:

            _report_sequences[vehicle_id] = 0


        _report_sequences[vehicle_id] += 1

        return _report_sequences[vehicle_id]


# ============================================================
# STATUS
# ============================================================

def get_request_sequence():

    with _lock:

        return _request_sequence


def get_report_sequence(vehicle_id):

    with _lock:

        return _report_sequences.get(
            vehicle_id,
            0
        )


# ============================================================
# LOCAL TEST RESET
# ============================================================

def reset_sequences():

    global _request_sequence

    with _lock:

        _request_sequence = 0

        _report_sequences.clear()