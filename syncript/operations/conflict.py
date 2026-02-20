"""
Conflict detection and handling
"""
import hashlib
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from ..core.ssh_manager import SSHManager
from ..config import LOCAL_ROOT, REMOTE_ROOT, REMOTE_TMP, SSH_HOST, SSH_PORT, SSH_USER
from ..utils.logging import log, warn, vlog
from ..utils.file_utils import _md5_local


def check_existing_conflicts(dry_run: bool) -> bool:
    """
    Scan LOCAL_ROOT for leftover *.conflict / *.conflict-info files.
    If any are found, ask the user what to do:
      [c] remove them all and continue syncing
      [r] remove them and exit
      [x] exit without doing anything
    Returns True if sync should proceed, False if it should abort.
    Never touches the remote side.
    """
    conflict_files = sorted(
        p for p in LOCAL_ROOT.rglob("*")
        if ".conflict" in p.name and p.is_file()
    )
    if not conflict_files:
        return True

    print()
    warn(f"Found {len(conflict_files)} unresolved conflict file(s):")
    for p in conflict_files:
        print(f"    {p.relative_to(LOCAL_ROOT)}")
    print()
    print("  [c] Remove all conflict files and CONTINUE syncing")
    print("  [r] Remove all conflict files and EXIT")
    print("  [x] EXIT without changing anything")
    print()

    while True:
        try:
            choice = input("  Your choice [c/r/x]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "x"

        if choice == "x":
            log("Exiting — conflict files left untouched.")
            return False

        if choice in ("c", "r"):
            if not dry_run:
                for p in conflict_files:
                    p.unlink(missing_ok=True)
                log(f"  Removed {len(conflict_files)} conflict file(s).")
            else:
                log("  [DRY] Would remove conflict files.")
            if choice == "r":
                log("Exiting after cleanup.")
                return False
            return True  # "c" → continue

        print("  Please enter c, r, or x.")


def save_conflict(mgr: SSHManager, rel: str, local_path: Path,
                  remote_path: str, dry_run: bool,
                  conflict_reason: str = ""):
    """
    Handle a detected conflict between local and remote versions of a file.

    Steps:
      1. Download the remote file into a temp location.
      2. Compare MD5 hashes — if identical, skip (no real conflict).
      3. Save the remote copy locally as  <stem>.remote.<ts>.conflict<ext>
      4. Write a  <stem>.<ts>.conflict-info  with full diagnostics.
      *** Nothing is written to the remote server. ***
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if dry_run:
        log(f"  [CONFLICT-DRY] {rel}  reason: {conflict_reason or 'both sides changed'}")
        return

    # ── Download remote file for comparison ─────────────────────────────────
    remote_tar = f"{REMOTE_TMP}/sync_conflict_{uuid.uuid4().hex}.tar.gz"
    tmp_tar = Path(tempfile.mktemp(suffix=".tar.gz"))
    remote_bytes: bytes = b""
    try:
        pack_cmd = (
            f"cd '{REMOTE_ROOT}' && "
            f"tar czf '{remote_tar}' --no-recursion '{rel}' 2>&1"
        )
        mgr.exec(pack_cmd, timeout=30)
        mgr.sftp_get(remote_tar, str(tmp_tar))
        with tarfile.open(tmp_tar, "r:gz") as tar:
            member = tar.getmembers()[0]
            with tar.extractfile(member) as src_f:
                remote_bytes = src_f.read()
    finally:
        tmp_tar.unlink(missing_ok=True)
        try:
            mgr.sftp_remove(remote_tar)
        except Exception:
            pass

    # ── MD5 comparison — skip if content is identical ───────────────────────
    local_md5 = _md5_local(local_path)
    remote_md5 = hashlib.md5(remote_bytes).hexdigest()

    if local_md5 == remote_md5:
        vlog(f"  [CONFLICT-SKIP] {rel}  MD5 match ({local_md5[:8]}…) — content identical")
        return

    # ── Save remote copy and info file locally only ──────────────────────────
    conflict_copy = local_path.with_name(
        local_path.stem + f".remote.{ts}.conflict" + local_path.suffix
    )
    info_file = local_path.with_name(
        local_path.stem + f".{ts}.conflict-info"
    )

    conflict_copy.write_bytes(remote_bytes)

    reason_line = conflict_reason or "Both sides were modified since the last sync."
    info_file.write_text(
        f"SYNC CONFLICT — {ts}\n"
        f"{'─' * 60}\n"
        f"  File   : {rel}\n"
        f"  Local  : {local_path}\n"
        f"  Remote : {SSH_USER}@{SSH_HOST}:{SSH_PORT}:{remote_path}\n"
        f"\n"
        f"Conflict reason:\n"
        f"  {reason_line}\n"
        f"\n"
        f"MD5 hashes:\n"
        f"  local  : {local_md5}\n"
        f"  remote : {remote_md5}\n"
        f"\n"
        f"Remote copy saved as:\n"
        f"  {conflict_copy.name}\n"
        f"\n"
        f"To resolve:\n"
        f"  1. Open both files in IntelliJ → View → Compare Files\n"
        f"     (or Git → Compare with… for any file, even non-git)\n"
        f"  2. Merge changes manually into the local file.\n"
        f"  3. Delete the .conflict and .conflict-info files.\n"
        f"  4. Run sync again.\n",
        encoding="utf-8",
    )

    log(f"  [CONFLICT] {rel}")
    log(f"    reason      : {reason_line}")
    log(f"    local MD5   : {local_md5}")
    log(f"    remote MD5  : {remote_md5}")
    log(f"    local kept  : {local_path.name}")
    log(f"    remote copy : {conflict_copy.name}")
    log(f"    info file   : {info_file.name}")
