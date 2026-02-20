"""
Progress file management (checkpoint for current sync run)
"""
import json
from ..config import PROGRESS_FILE


def load_progress() -> dict:
    """
    Format: {"pushed": [...], "pulled": [...], "deleted_r": [...], "deleted_l": [...]}
    """
    if PROGRESS_FILE.exists():
        try:
            return json.loads(PROGRESS_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {"pushed": [], "pulled": [], "deleted_r": [], "deleted_l": []}


def save_progress(prog: dict):
    """Save progress to checkpoint file"""
    PROGRESS_FILE.write_text(json.dumps(prog, indent=2), "utf-8")


def clear_progress():
    """Remove progress file after successful sync"""
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()
