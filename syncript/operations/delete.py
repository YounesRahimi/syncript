"""
Delete operations (local and remote)
"""
from typing import Optional
from pathlib import PurePosixPath
from ..core.ssh_manager import SSHManager
from ..config import REMOTE_ROOT
from ..utils.logging import log, warn
from ..state.state_manager import save_state
from ..state.progress_manager import save_progress


def _confirm_deletions_by_leaf(rel_paths: list[str], *, context: str = "remote") -> Optional[list[str]]:
    """
    Group rel_paths by their parent directory (leaf) and ask the user once per leaf
    whether to delete the files in that directory.

    Returns:
      - list of rel_paths the user confirmed for deletion
      - empty list if none confirmed
      - None if the user chose to abort (quit)
    """
    if not rel_paths:
        return []

    groups: dict[str, list[str]] = {}
    for rel in sorted(rel_paths):
        parent = PurePosixPath(rel).parent.as_posix()
        if parent == ".":
            parent = "."
        groups.setdefault(parent, []).append(rel)

    confirmed: list[str] = []
    accept_all = False

    print()
    for parent, items in sorted(groups.items()):
        if accept_all:
            confirmed.extend(items)
            continue

        print(f"Directory: {parent}  — {len(items)} file(s)")
        # Show up to 10 sample files to avoid huge dumps
        for p in items[:10]:
            print(f"  {p}")
        if len(items) > 10:
            print(f"  ... ({len(items) - 10} more)")

        while True:
            try:
                choice = input("  Delete these files? [y/N/all/quit]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = "n"

            if choice in ("", "n", "no"):
                # skip this group
                break
            if choice in ("y", "yes"):
                confirmed.extend(items)
                break
            if choice in ("all", "a"):
                confirmed.extend(items)
                accept_all = True
                break
            if choice in ("quit", "q"):
                log("Deletion aborted by user.")
                return None
            print("  Please enter y, n, all, or quit.")

    return confirmed


def delete_remote(mgr: SSHManager, rel_paths: list[str],
                  dry_run: bool, state: dict, prog: dict):
    """Delete files on remote with user confirmation"""
    if not rel_paths:
        return
    if dry_run:
        for rel in rel_paths:
            log(f"  [DEL-REMOTE-DRY] {rel}")
        return

    # Ask user per-leaf-directory which files to actually delete.
    confirmed = _confirm_deletions_by_leaf(rel_paths, context="remote")
    if confirmed is None:
        log("Remote deletions skipped by user.")
        return
    if not confirmed:
        log("No remote deletions confirmed.")
        return

    # Batch delete with one `rm` command for the confirmed set
    quoted = " ".join(f"'{REMOTE_ROOT}/{r}'" for r in confirmed)
    cmd = f"rm -f {quoted}"
    try:
        mgr.exec(cmd, timeout=30)
        for rel in confirmed:
            state.pop(rel, None)
            prog.setdefault("deleted_r", []).append(rel)
            log(f"  [DEL-REMOTE ✓] {rel}")
        save_progress(prog)
        save_state(state)
    except Exception as exc:
        warn(f"Batch remote delete failed: {exc}; will try one-by-one")
        for rel in confirmed:
            try:
                mgr.sftp_remove(f"{REMOTE_ROOT}/{rel}")
                state.pop(rel, None)
                prog.setdefault("deleted_r", []).append(rel)
                log(f"  [DEL-REMOTE ✓] {rel}")
            except Exception as e2:
                warn(f"  could not delete remote {rel}: {e2}")
        save_progress(prog)
        save_state(state)
