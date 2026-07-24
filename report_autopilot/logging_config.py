"""
logging_config.py
------------------
One place to configure logging for the whole package. Every module
gets its logger via `logging.getLogger("report_autopilot.<module>")`,
and this file decides where those messages actually go.

Why this matters for production: `print()` statements disappear the
moment the process ends, and nobody can tell what happened at 3am when
a scheduled report silently failed. This writes to both the console
(for interactive runs) and a rotating log file (so history survives
and doesn't grow unbounded), and includes a timestamp + module name on
every line so a failure can actually be traced back to where it
happened.
"""

import logging
import logging.handlers
import os


def setup_logging(log_dir: str = "logs", level=logging.INFO):
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "report_autopilot.log")

    root_logger = logging.getLogger("report_autopilot")
    root_logger.setLevel(level)

    if root_logger.handlers:
        # Already configured (e.g. setup_logging called twice in one
        # process) -- don't double-attach handlers, which would print
        # every message multiple times.
        return root_logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)

    # Rotates at 5MB, keeps 5 old files -- bounded disk usage even if
    # this runs unattended for months.
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    root_logger.addHandler(file_handler)

    return root_logger
