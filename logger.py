# ============================================================
# AQUAHOLICS ROBOTX 2026
# OCS LOGGER
# ============================================================

import logging
import os
import time

import config


def get_logger():

    logger = logging.getLogger(
        "aquaholics_ocs"
    )


    if logger.handlers:

        return logger


    os.makedirs(
        config.OCS_LOG_DIR,
        exist_ok=True
    )


    filename = (
        "aquaholics_ocs_"
        + time.strftime(
            "%Y%m%d_%H%M%S"
        )
        + ".log"
    )


    log_path = os.path.join(
        config.OCS_LOG_DIR,
        filename
    )


    logger.setLevel(
        logging.INFO
    )


    formatter = logging.Formatter(

        "%(asctime)s "
        "%(levelname)s "
        "%(message)s"

    )


    file_handler = logging.FileHandler(

        log_path,

        encoding="utf-8"

    )


    file_handler.setFormatter(
        formatter
    )


    logger.addHandler(
        file_handler
    )


    logger.propagate = False


    logger.info(
        "AQUAHOLICS OCS log started"
    )

    logger.info(
        "Log file: %s",
        log_path
    )


    return logger