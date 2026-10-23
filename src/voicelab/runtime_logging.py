"""Listener diagnostics deliberately exclude request/provider exception contents.

Uvicorn websocket handshake logs use uvicorn.error, independently of access_log.
Only a fixed severity message is emitted; protected metadata is a separate channel.
No logging configuration is applied by importing this module.
"""

from __future__ import annotations

import logging


class RuntimeDiagnosticFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Do not interpolate message/args, pathname, exception or stack text:
        # websocket URLs can contain the nonce even outside access logging.
        severity = {
            logging.WARNING: "WARNING", logging.ERROR: "ERROR",
            logging.CRITICAL: "CRITICAL",
        }.get(record.levelno, "DIAGNOSTIC")
        return f"{severity}: relay runtime diagnostic; request details suppressed"


def listener_logging_config() -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"safe": {"()": RuntimeDiagnosticFormatter}},
        "handlers": {"safe": {
            "class": "logging.StreamHandler", "formatter": "safe",
            "stream": "ext://sys.stderr",
        }},
        "root": {"handlers": ["safe"], "level": "WARNING"},
        "loggers": {
            "uvicorn": {"handlers": ["safe"], "level": "WARNING", "propagate": False},
            "uvicorn.error": {"handlers": ["safe"], "level": "WARNING", "propagate": False},
            "uvicorn.access": {"handlers": [], "level": "CRITICAL", "propagate": False},
        },
    }
