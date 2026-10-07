"""Tunable settings for the decision layer.

Everything here is OUR engineering choice. None of it comes from the RobotX
handbook, and none of it is part of the Task 1 rules table (rules.py).
These values are placeholders until they are tuned with real Section 1 data,
and they move into config/settings.yaml later.
"""
from __future__ import annotations

# Minimum marker_confidence for a known marker type. Below this, the
# interpretation becomes UNKNOWN (reason LOW_CONFIDENCE).
# PLACEHOLDER: 0.5 has no data behind it. Tune after measuring how Section 1's
# confidence values behave on real recordings.
DEFAULT_MIN_MARKER_CONFIDENCE = 0.5