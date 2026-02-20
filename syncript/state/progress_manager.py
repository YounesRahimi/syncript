"""
Progress file management (checkpoint for current sync run)
"""
import json
from ..config import get_progress_file


def load_progress() -> dict:
    """
    Format: {"pushed": [...], "pulled": [...], "deleted_r": [...], "deleted_l": [...]}
    """
    if get_progress_file().exists():
        try:
            return json.loads(get_progress_file().read_text("utf-8"))
        except Exception:
            pass
    return {"pushed": [], "pulled": [], "deleted_r": [], "deleted_l": []}


def save_progress(prog: dict):
    """Save progress to checkpoint file"""
    get_progress_file().write_text(json.dumps(prog, indent=2), "utf-8")


def clear_progress():
    """Remove progress file after successful sync"""
    if get_progress_file().exists():
        get_progress_file().unlink()
