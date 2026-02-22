"""
File transfer operations (push and pull batches)
"""
import os
import shutil
import tarfile
import tempfile
import uuid
import base64
from pathlib import Path
from ..core.ssh_manager import SSHManager
from .. import config as _cfg
from ..utils.logging import log
from ..state.state_manager import save_state
from ..state.progress_manager import save_progress


def push_batch(mgr: SSHManager, files: list[tuple[str, Path]],
               dry_run: bool, state: dict, prog: dict) -> tuple[int, int]:
    """
    Push a list of (rel_path, local_Path) to remote as one tar.gz.
    Each successfully transferred file is immediately checkpointed.
    Returns (compressed_bytes, uncompressed_bytes) for adaptive batch sizing.
    """
    if not files:
        return 0, 0
    if dry_run:
        for rel, _ in files:
            log(f"  [PUSH-DRY] {rel}")
        return 0, 0

    tmp_tar = Path(tempfile.mktemp(suffix=".tar.gz"))
    remote_tar = f"{_cfg.REMOTE_TMP}/sync_push_{uuid.uuid4().hex}.tar.gz"

    try:
        # ── Pack ────────────────────────────────────────────────────────────
        log(f"  [PUSH] packing {len(files)} file(s) into {tmp_tar.name} …")
        uncompressed = 0
        with tarfile.open(tmp_tar, "w:gz", compresslevel=6) as tar:
            for rel, lpath in files:
                tar.add(str(lpath), arcname=rel)
                try:
                    uncompressed += lpath.stat().st_size
                except OSError:
                    pass
        compressed = tmp_tar.stat().st_size
        size_kb = compressed // 1024
        log(f"  [PUSH] packed → {size_kb} KB")

        # ── Upload ──────────────────────────────────────────────────────────
        log(f"  [PUSH] uploading {tmp_tar.name} → remote …")
        mgr.sftp_put(str(tmp_tar), remote_tar)

        # ── Extract on remote ───────────────────────────────────────────────
        extract_cmd = (
            f"cd '{_cfg.REMOTE_ROOT}' && "
            f"tar xzf '{remote_tar}' --no-same-owner 2>&1"
        )
        log(f"  [PUSH] extracting on remote …")
        mgr.exec(extract_cmd, timeout=120)

        # ── Checkpoint each file ────────────────────────────────────────────
        for rel, lpath in files:
            st = lpath.stat()
            state[rel] = {
                "lmtime": st.st_mtime, "lsize": st.st_size,
                "rmtime": st.st_mtime, "rsize": st.st_size,
            }
            prog["pushed"].append(rel)
            log(f"  [PUSH ✓] {rel}")
        save_progress(prog)
        save_state(state)
        return compressed, uncompressed

    finally:
        tmp_tar.unlink(missing_ok=True)
        try:
            mgr.sftp_remove(remote_tar)
        except Exception:
            pass


def pull_batch(mgr: SSHManager, files: list[str],
               dry_run: bool, state: dict, prog: dict,
               remote_meta: dict[str, tuple[float, int]]) -> tuple[int, int]:
    """
    Pull a list of rel_paths from remote as one tar.gz.
    Returns (compressed_bytes, uncompressed_bytes) for adaptive batch sizing.
    """
    if not files:
        return 0, 0
    if dry_run:
        for rel in files:
            log(f"  [PULL-DRY] {rel}")
        return 0, 0

    remote_tar = f"{_cfg.REMOTE_TMP}/sync_pull_{uuid.uuid4().hex}.tar.gz"
    tmp_tar = Path(tempfile.mktemp(suffix=".tar.gz"))

    try:
        # ── Pack on remote ──────────────────────────────────────────────────
        # Build a null-delimited file list to avoid shell quoting issues
        file_list_remote = f"{_cfg.REMOTE_TMP}/sync_filelist_{uuid.uuid4().hex}.txt"
        file_list_content = "\n".join(files)

        log(f"  [PULL] requesting remote to pack {len(files)} file(s) …")

        # Write file list to remote via echo (chunked to avoid ARG_MAX)
        _write_remote_file(mgr, file_list_remote, file_list_content)

        pack_cmd = (
            f"cd '{_cfg.REMOTE_ROOT}' && "
            f"tar czf '{remote_tar}' --no-recursion -T '{file_list_remote}' "
            f"--ignore-failed-read 2>&1"
        )
        mgr.exec(pack_cmd, timeout=120)

        # ── Download ────────────────────────────────────────────────────────
        log(f"  [PULL] downloading …")
        mgr.sftp_get(remote_tar, str(tmp_tar))
        compressed = tmp_tar.stat().st_size
        size_kb = compressed // 1024
        log(f"  [PULL] downloaded {size_kb} KB, extracting …")

        # ── Extract locally ─────────────────────────────────────────────────
        with tarfile.open(tmp_tar, "r:gz") as tar:
            for member in tar.getmembers():
                dest = _cfg.LOCAL_ROOT / member.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src_f, open(dest, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                # Restore remote mtime
                if member.name in remote_meta:
                    rmt = int(remote_meta[member.name][0])
                    os.utime(dest, (rmt, rmt))

        # ── Checkpoint ──────────────────────────────────────────────────────
        uncompressed = 0
        for rel in files:
            lpath = _cfg.LOCAL_ROOT / rel
            if lpath.exists():
                st = lpath.stat()
                uncompressed += st.st_size
                rmtime, rsize = remote_meta.get(rel, (st.st_mtime, st.st_size))
                state[rel] = {
                    "lmtime": st.st_mtime, "lsize": st.st_size,
                    "rmtime": rmtime, "rsize": rsize,
                }
                prog["pulled"].append(rel)
                log(f"  [PULL ✓] {rel}")
        save_progress(prog)
        save_state(state)
        return compressed, uncompressed

    finally:
        tmp_tar.unlink(missing_ok=True)
        try:
            mgr.sftp_remove(remote_tar)
        except Exception:
            pass
        try:
            mgr.sftp_remove(file_list_remote)
        except Exception:
            pass


def _write_remote_file(mgr: SSHManager, remote_path: str, content: str):
    """Write a text file to remote by piping content through SSH exec."""
    # Use base64 to avoid any quoting/special-char issues
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    cmd = f"echo '{b64}' | base64 -d > '{remote_path}'"
    mgr.exec(cmd, timeout=30)
