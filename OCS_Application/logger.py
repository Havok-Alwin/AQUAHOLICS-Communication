# AQUAHOLICS ROBOTX 2026 OCS LOGGER
import logging
import os
import time

import config


def get_logger():
    logger = logging.getLogger("aquaholics_ocs")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        os.makedirs(config.OCS_LOG_DIR, exist_ok=True)
        filename = "aquaholics_ocs_" + time.strftime("%Y%m%d_%H%M%S") + ".log"
        path = os.path.join(config.OCS_LOG_DIR, filename)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        safe_log(logger, logging.INFO, "AQUAHOLICS OCS log started | path=%s", path)
    except Exception:
        # Console status and MQTT processing remain available if the log cannot be opened.
        logger.addHandler(logging.NullHandler())
    return logger


def safe_log(logger, level, message, *args):
    """Best-effort logging; logger failures must not interrupt MQTT handling."""
    try:
        logger.log(level, message, *args)
    except Exception:
        pass
