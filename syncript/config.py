"""
Configuration constants for syncript
Author: Younes Rahimi
"""
import os
from pathlib import Path, PurePosixPath
from typing import Optional

# ══════════════════════════════════════════════════════════════════════════════
#  DEFAULTS  ── overridden by YAML config or apply_profile()
# ══════════════════════════════════════════════════════════════════════════════

SSH_HOST = "example.com"
SSH_PORT = 22
SSH_USER = "root"
# Path to your private key, or None to use ssh-agent / ~/.ssh/id_*
SSH_KEY_PATH: Optional[str] = None  # e.g. r"C:\Users\bs\.ssh\id_rsa"
SSH_PASSWORD: Optional[str] = None  # only if you use password auth

LOCAL_ROOT = Path(".")
REMOTE_ROOT = PurePosixPath("/")

STIGNORE_FILE = "./.stignore"

# Remote temp dir for scan output + batch tarballs
REMOTE_TMP = "/tmp"

# Retry settings
RETRY_MAX = 5
RETRY_BASE_DELAY = 2.0  # seconds; doubles each attempt

# mtime tolerance (seconds) — 3 min covers FAT/NTFS granularity + minor clock skew
MTIME_TOLERANCE = 180

# Maximum compressed size (bytes) per tar.gz batch (push or pull).
# Files are accumulated until the estimated compressed size reaches this limit.
# Default: 512 KB
BATCH_FILE_SIZE = 512 * 1024


# ══════════════════════════════════════════════════════════════════════════════
#  DYNAMIC PATHS  ── computed from LOCAL_ROOT at call time
# ══════════════════════════════════════════════════════════════════════════════

def get_state_file() -> Path:
    """Return the state file path based on the current LOCAL_ROOT."""
    return LOCAL_ROOT / ".sync_state.csv"


def get_progress_file() -> Path:
    """Return the progress file path based on the current LOCAL_ROOT."""
    return LOCAL_ROOT / ".sync_progress.json"


def get_skipped_deletions_file() -> Path:
    """Return the skipped-deletions file path based on the current LOCAL_ROOT."""
    return LOCAL_ROOT / ".sync_skipped_deletions.json"


# ══════════════════════════════════════════════════════════════════════════════
#  GLOBAL CONFIG FILE  ── $XDG_CONFIG_HOME/syncript/config.yaml
# ══════════════════════════════════════════════════════════════════════════════

def get_global_config_dir() -> Path:
    """Return the global config directory for syncript."""
    xdg = os.environ.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg) / "syncript"
    if os.name == "nt":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            return Path(appdata) / "syncript"
    return Path.home() / ".config" / "syncript"


def load_global_config() -> dict:
    """Load global config from the syncript config directory."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        return {}
    cfg_path = get_global_config_dir() / "config.yaml"
    if not cfg_path.is_file():
        return {}
    try:
        with cfg_path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
#  PROJECT CONFIG FILE  ── .syncript (searched upward)
# ══════════════════════════════════════════════════════════════════════════════

def find_syncript(start: Optional[Path] = None) -> Optional[Path]:
    """
    Search upward from *start* (default: cwd) for a .syncript YAML file.
    Returns the Path if found, or None if no .syncript exists in any parent.
    """
    current = (start or Path.cwd()).resolve()
    while True:
        candidate = current / ".syncript"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_syncript_file(path: Path) -> dict:
    """Parse a .syncript YAML file and return its contents as a dict."""
    try:
        import yaml  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "PyYAML is required for YAML config support. "
            "Install it with: pip install pyyaml"
        )
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_profile(data: dict, profile_name: str = "default") -> dict:
    """
    Extract a named profile from a .syncript or config.yaml data dict.
    Falls back to the first profile if the named one is not found.
    Returns a flat profile dict merged with top-level defaults.
    """
    defaults = data.get("defaults", {})
    profiles = data.get("profiles", [])
    if not profiles:
        return defaults.copy()
    # Find by name
    profile = next((p for p in profiles if p.get("name") == profile_name), None)
    if profile is None:
        profile = profiles[0]
    merged = defaults.copy()
    merged.update(profile)
    return merged


# ══════════════════════════════════════════════════════════════════════════════
#  APPLY PROFILE  ── mutates module-level variables
# ══════════════════════════════════════════════════════════════════════════════

def apply_profile(profile: dict):
    """
    Apply a profile dict to the module-level config variables.
    Supports keys: server, port, user, ssh_key, local_root, remote_root,
                   base_remote (prepended to remote_root if remote_root is relative),
                   batch_file_size (max compressed bytes per tar.gz batch).
    """
    global SSH_HOST, SSH_PORT, SSH_USER, SSH_KEY_PATH, SSH_PASSWORD
    global LOCAL_ROOT, REMOTE_ROOT, BATCH_FILE_SIZE

    if "server" in profile:
        SSH_HOST = str(profile["server"])
    if "port" in profile:
        SSH_PORT = int(profile["port"])
    if "user" in profile:
        SSH_USER = str(profile["user"])
    elif "username" in profile:
        SSH_USER = str(profile["username"])
    if "ssh_key" in profile:
        SSH_KEY_PATH = str(profile["ssh_key"]) if profile["ssh_key"] else None
    if "ssh_password" in profile:
        SSH_PASSWORD = str(profile["ssh_password"]) if profile["ssh_password"] else None
    if "local_root" in profile:
        LOCAL_ROOT = Path(profile["local_root"]).expanduser().resolve()
    if "remote_root" in profile:
        rr = str(profile["remote_root"])
        base = str(profile.get("base_remote", "")).rstrip("/")
        if base and not rr.startswith("/"):
            rr = f"{base}/{rr}"
        REMOTE_ROOT = PurePosixPath(rr)
    if "batch_file_size" in profile:
        BATCH_FILE_SIZE = int(profile["batch_file_size"])
