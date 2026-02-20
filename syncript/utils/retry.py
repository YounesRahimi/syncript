"""
Retry decorator for network operations
"""
import functools
import time
from .logging import log, warn
from ..config import RETRY_MAX, RETRY_BASE_DELAY


def retried(fn):
    """Decorator: retry fn up to RETRY_MAX times with exponential back-off."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        delay = RETRY_BASE_DELAY
        for attempt in range(1, RETRY_MAX + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                if attempt == RETRY_MAX:
                    raise
                warn(f"{fn.__name__} failed (attempt {attempt}/{RETRY_MAX}): {exc}")
                log(f"  retrying in {delay:.0f}s …")
                time.sleep(delay)
                delay = min(delay * 2, 60)
                # Try to reconnect if the connection object is the first arg
                # (works for bound methods of SSHManager)

    return wrapper
