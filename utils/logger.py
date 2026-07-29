"""
GoldPulse Alerts — Logging
============================
Centralized logging with:
- Console output (colored for dev, plain for prod)
- Rotating file handler (keeps logs manageable on a VPS)
- Separate error log file for quick debugging
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from config.settings import LOG_LEVEL, LOG_DIR


def setup_logging() -> logging.Logger:
    """
    Configure the root logger for GoldPulse.

    Returns:
        The configured root logger.
    """
    # Create log directory if it doesn't exist
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"WARNING: Failed to create log directory {LOG_DIR}: {e}", file=sys.stderr)

    # Root logger
    root_logger = logging.getLogger("goldpulse")
    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Prevent duplicate handlers on reload
    if root_logger.handlers:
        return root_logger

    # ── Formatters ────────────────────────────────────────────────────
    detailed_fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Console Handler (stdout) ─────────────────────────────────────
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # ── Rotating File Handler (all logs) ──────────────────────────────
    # 5 MB per file, keep 5 backups → max ~25 MB total
    file_handler = RotatingFileHandler(
        LOG_DIR / "goldpulse.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed_fmt)
    root_logger.addHandler(file_handler)

    # ── Rotating File Handler (errors only) ───────────────────────────
    error_handler = RotatingFileHandler(
        LOG_DIR / "goldpulse_errors.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(detailed_fmt)
    root_logger.addHandler(error_handler)

    root_logger.info("Logging initialized — level=%s, dir=%s", LOG_LEVEL, LOG_DIR)
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """
    Get a child logger for a specific module.

    Args:
        name: Module name (e.g., 'ingestion.rss', 'bot.handlers')

    Returns:
        A child logger under the 'goldpulse' namespace.
    """
    return logging.getLogger(f"goldpulse.{name}")
