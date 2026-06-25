"""Centralized human-facing stderr output for otto-workbench AI scripts.

NOT for structured event logging — use trail.py for that.
"""

from __future__ import annotations

import os
import sys
import threading

_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stderr.isatty()

ANSI_DIM = "" if _NO_COLOR else "\033[2m"
ANSI_BOLD = "" if _NO_COLOR else "\033[1m"
ANSI_RESET = "" if _NO_COLOR else "\033[0m"

ANSI_INFO = "▸" if _NO_COLOR else "\033[1;34m▸\033[0m"
ANSI_WARN = "⚠" if _NO_COLOR else "\033[1;33m⚠\033[0m"
ANSI_ERR = "✗" if _NO_COLOR else "\033[1;31m✗\033[0m"
ANSI_OK = "✓" if _NO_COLOR else "\033[1;32m✓\033[0m"
ANSI_INFO_DOT = "●" if _NO_COLOR else "\033[1;34m●\033[0m"
ANSI_WARN_DOT = "●" if _NO_COLOR else "\033[1;33m●\033[0m"
ANSI_ERR_DOT = "●" if _NO_COLOR else "\033[1;31m●\033[0m"

_print_lock = threading.Lock()


def info(msg: str) -> None:
    with _print_lock:
        print(f"{ANSI_INFO} {msg}", file=sys.stderr, flush=True)


def warn(msg: str) -> None:
    with _print_lock:
        print(f"{ANSI_WARN} {msg}", file=sys.stderr, flush=True)


def error(msg: str) -> None:
    with _print_lock:
        print(f"{ANSI_ERR} {msg}", file=sys.stderr, flush=True)


def ok(msg: str) -> None:
    with _print_lock:
        print(f"{ANSI_OK} {msg}", file=sys.stderr, flush=True)


def dim(msg: str) -> None:
    with _print_lock:
        print(f"  {ANSI_DIM}{msg}{ANSI_RESET}", file=sys.stderr, flush=True)


def blank() -> None:
    with _print_lock:
        print(file=sys.stderr)
