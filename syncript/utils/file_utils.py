"""
File utilities (MD5, comparison)
"""
import hashlib
from pathlib import Path
from typing import Optional, TYPE_CHECKING
from .. import config as _cfg

if TYPE_CHECKING:
    from ..core.ssh_manager import SSHManager


def _md5_local(path: Path) -> str:
    """Compute MD5 hash of a local file"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _md5_remote(mgr: "SSHManager", remote_path: str) -> str:
    """Return MD5 hex digest of a remote file using md5sum."""
    out, _ = mgr.exec(f"md5sum '{remote_path}'", timeout=30)
    # md5sum output: "<hash>  <filename>"
    return out.strip().split()[0]


def _file_changed(new_mtime: float, new_size: int,
                  old_mtime: Optional[float], old_size: Optional[int]) -> bool:
    """True if the file is different from what we last recorded."""
    if old_mtime is None:
        return True
    mtime_diff = abs(new_mtime - old_mtime)
    return mtime_diff > _cfg.MTIME_TOLERANCE or new_size != old_size
