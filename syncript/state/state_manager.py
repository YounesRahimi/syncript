"""
State file management (persistent across runs)
"""
import json
import csv
import io
from ..config import get_state_file, get_skipped_deletions_file
from ..utils.logging import warn


def load_state() -> dict:
    """
    Legacy-compatible loader.

    In-memory format stays:
      { rel_path: {"lmtime": float, "lsize": int, "rmtime": float, "rsize": int}, ... }

    On-disk:
      - If file starts with '{' -> treat as legacy JSON and parse via json.loads
      - Otherwise treat as CSV with header: rel,lmtime,lsize,rmtime,rsize
    """
    if not get_state_file().exists():
        return {}

    try:
        text = get_state_file().read_text("utf-8")
    except Exception:
        return {}

    s = text.lstrip()
    # Legacy JSON detection & parse (for backward compatibility / migration)
    if s.startswith("{"):
        try:
            return json.loads(text)
        except Exception:
            # fallthrough to try CSV parsing below
            pass

    # Parse CSV
    result: dict = {}
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            rel = row.get("rel")
            if not rel:
                continue
            try:
                lmtime = float(row.get("lmtime") or 0.0)
            except Exception:
                lmtime = 0.0
            try:
                lsize = int(row.get("lsize") or 0)
            except Exception:
                lsize = 0
            try:
                rmtime = float(row.get("rmtime") or 0.0)
            except Exception:
                rmtime = 0.0
            try:
                rsize = int(row.get("rsize") or 0)
            except Exception:
                rsize = 0
            result[rel] = {
                "lmtime": lmtime,
                "lsize": lsize,
                "rmtime": rmtime,
                "rsize": rsize,
            }
    except Exception:
        return {}

    return result


def save_state(state: dict):
    """
    Write state as CSV for compactness.

    Header: rel,lmtime,lsize,rmtime,rsize
    """
    try:
        with get_state_file().open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["rel", "lmtime", "lsize", "rmtime", "rsize"])
            for rel in sorted(state.keys()):
                entry = state[rel]
                # Use repr for floats to preserve precision; ints written directly
                lmtime = entry.get("lmtime", "")
                lsize = entry.get("lsize", "")
                rmtime = entry.get("rmtime", "")
                rsize = entry.get("rsize", "")
                writer.writerow([
                    rel,
                    "" if lmtime == "" else repr(lmtime),
                    "" if lsize == "" else int(lsize),
                    "" if rmtime == "" else repr(rmtime),
                    "" if rsize == "" else int(rsize),
                ])
    except Exception:
        # Best effort: don't crash the sync if state save fails
        warn("Failed to save state file (CSV).")


def load_skipped_deletions() -> set:
    """Load the set of rel paths the user chose not to delete."""
    f = get_skipped_deletions_file()
    if not f.exists():
        return set()
    try:
        data = json.loads(f.read_text("utf-8"))
        return set(data.get("skipped", []))
    except Exception:
        return set()


def save_skipped_deletions(skipped: set):
    """Persist the skipped-deletions set."""
    try:
        get_skipped_deletions_file().write_text(
            json.dumps({"skipped": sorted(skipped)}, indent=2), "utf-8"
        )
    except Exception:
        warn("Failed to save skipped deletions file.")


def remove_skipped_deletions(rel_paths: list):
    """Remove entries from the skipped-deletions set (e.g. file reappeared)."""
    if not rel_paths:
        return
    skipped = load_skipped_deletions()
    skipped -= set(rel_paths)
    save_skipped_deletions(skipped)
