"""
Logging utilities for syncript
"""
from datetime import datetime

_verbose = False


def set_verbose(verbose: bool):
    """Set the verbose flag"""
    global _verbose
    _verbose = verbose


def log(msg: str):
    """Log a message with timestamp"""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def vlog(msg: str):
    """Log a verbose message (only if verbose mode is enabled)"""
    if _verbose:
        log(msg)


def warn(msg: str):
    """Log a warning message"""
    log(f"⚠  {msg}")
