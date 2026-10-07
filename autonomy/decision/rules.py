"""Task 1 Core beacon meanings: the ONE place these rules live.

Source: RobotX 2026 Team Handbook, Task 1 Safe Passage (version dated
2026-08-07). Re-check against the latest handbook before the event.

The table says only WHAT a buoy is. It does not say what to do about it:
passing sides (red = starboard, green = port) and circle directions
(entry = clockwise, exit = counterclockwise) belong to later modules.

A combination that is not in the table has no meaning (UNKNOWN).
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Tuple

from interfaces.messages import LightColor, LightState, MarkerType, ObjectClass

RuleKey = Tuple[ObjectClass, LightColor, LightState]


def _build_rules() -> Mapping[RuleKey, MarkerType]:
    rules = {
        (ObjectClass.TASK1_BUOY, LightColor.RED, LightState.FLASHING):
            MarkerType.RED,
        (ObjectClass.TASK1_BUOY, LightColor.GREEN, LightState.FLASHING):
            MarkerType.GREEN,
        (ObjectClass.TASK1_BUOY, LightColor.BLUE, LightState.FLASHING):
            MarkerType.ENTRY,
        (ObjectClass.TASK1_BUOY, LightColor.BLUE, LightState.STEADY):
            MarkerType.EXIT,
        (ObjectClass.TASK1_BUOY, LightColor.NONE, LightState.OFF):
            MarkerType.OBSTACLE,
    }
    # UNKNOWN_OBSTACLE + any light data -> OBSTACLE
    for color in LightColor:
        for state in LightState:
            rules[(ObjectClass.UNKNOWN_OBSTACLE, color, state)] = \
                MarkerType.OBSTACLE
    return MappingProxyType(rules)  # read-only view


TASK1_CORE_RULES: Mapping[RuleKey, MarkerType] = _build_rules()