"""
Configuration constants for syncript
"""
from pathlib import Path, PurePosixPath
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG  ── edit these
# ══════════════════════════════════════════════════════════════════════════════

SSH_HOST = "136.0.10.24"
SSH_PORT = 9011
SSH_USER = "root"
# Path to your private key, or None to use ssh-agent / ~/.ssh/id_*
SSH_KEY_PATH: Optional[str] = None  # e.g. r"C:\Users\bs\.ssh\id_rsa"
SSH_PASSWORD: Optional[str] = None  # only if you use password auth

LOCAL_ROOT = Path(r"C:\Users\bs\projects\jibit\cloud")
REMOTE_ROOT = PurePosixPath("/root/projects/jibit/cloud")

STIGNORE_FILE = "./.stignore"
STATE_FILE = LOCAL_ROOT / ".sync_state.csv"
PROGRESS_FILE = LOCAL_ROOT / ".sync_progress.json"

# Remote temp dir for scan output + batch tarballs
REMOTE_TMP = "/tmp"

# Retry settings
RETRY_MAX = 5
RETRY_BASE_DELAY = 2.0  # seconds; doubles each attempt

# mtime tolerance (seconds) — 3 min covers FAT/NTFS granularity + minor clock skew
MTIME_TOLERANCE = 180
