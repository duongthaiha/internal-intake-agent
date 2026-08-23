"""Logging configuration shared by hosted-agent entry points."""

import logging
import os


LOG_LEVEL_NAMES = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def get_log_level(log_level: str | None = None) -> str:
    level_name = (log_level or os.getenv("LOG_LEVEL", "INFO")).upper()
    if level_name not in LOG_LEVEL_NAMES:
        valid_levels = ", ".join(LOG_LEVEL_NAMES)
        raise RuntimeError(f"LOG_LEVEL must be one of: {valid_levels}.")
    return level_name


def configure_logging(log_level: str | None = None) -> str:
    level_name = get_log_level(log_level)
    logging.basicConfig(
        level=getattr(logging, level_name),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger(__name__).info("Log level set to %s", level_name)
    return level_name
