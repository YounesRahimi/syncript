#!/usr/bin/env python3
"""
syncript.py  —  Unstable-connection-tolerant bidirectional SSH sync
================================================================

Optimizations over v1:
  ① Remote scan is ASYNC via tmux + `find` (one fire-and-forget command).
    The script fires `find … > /tmp/sync_scan_<uuid>.tsv` inside tmux,
    then polls for the output file — no SFTP directory-walking round-trips.
  ② Files compared by mtime + size only (no MD5 / no reading remote content).
  ③ Transfers use tar+gzip batching:  push N files → ONE .tar.gz upload +
    ONE remote `tar x` command.  Pull N files → ONE remote `tar c` + download.
  ④ Checkpoint/resume: a .sync_progress.json tracks every completed transfer.
    A crashed/interrupted run restarts from exactly where it left off.
  ⑤ Every remote operation is wrapped in a retry-with-backoff decorator.
  ⑥ SSH keep-alive + auto-reconnect on drop.
  ⑦ Temp files on remote are UUID-named and always cleaned up.

Usage:
  python syncript.py [options]

Options:
  -n, --dry-run     Preview changes without applying them
  -v, --verbose     Show every file considered, not just actions
  -f, --force       Force full rescan (ignore state and progress cache)
  --push-only       Only push local→remote
  --pull-only       Only pull remote→local
  --poll-interval N Seconds between remote-scan polls (default: 5)
  --poll-timeout  N Max seconds to wait for remote scan (default: 120)
  -h, --help        Show this help

Requirements:
  pip install paramiko
"""

# ── stdlib ──────────────────────────────────────────────────────────────────
import argparse
import functools
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional

# ── third-party ─────────────────────────────────────────────────────────────
try:
    import paramiko
except ImportError:
    sys.exit(
        "ERROR: paramiko is not installed.\n"
        "Run:  pip install paramiko\n"
        "Then retry."
    )

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
STATE_FILE = LOCAL_ROOT / ".sync_state.json"
PROGRESS_FILE = LOCAL_ROOT / ".sync_progress.json"

# Remote temp dir for scan output + batch tarballs
REMOTE_TMP = "/tmp"

# Retry settings
RETRY_MAX = 5
RETRY_BASE_DELAY = 2.0  # seconds; doubles each attempt

# mtime tolerance (seconds) — 3 min covers FAT/NTFS granularity + minor clock skew
MTIME_TOLERANCE = 180

# ══════════════════════════════════════════════════════════════════════════════
#  LOGGING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

_verbose = False


def log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def vlog(msg: str):
    if _verbose:
        log(msg)


def warn(msg: str):
    log(f"⚠  {msg}")


# ══════════════════════════════════════════════════════════════════════════════
#  RETRY DECORATOR
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
#  SSH MANAGER  (keep-alive + auto-reconnect)
# ══════════════════════════════════════════════════════════════════════════════

class SSHManager:
    """
    Wraps paramiko SSHClient + SFTPClient.
    Automatically reconnects on channel errors.
    Sends SSH keep-alives to reduce mid-transfer drops.
    """

    def __init__(self):
        self._ssh: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None

    # ── connection ─────────────────────────────────────────────────────────

    def connect(self):
        if self._ssh:
            try:
                self._ssh.get_transport().send_ignore()  # test if alive
                return
            except Exception:
                self._close_quietly()

        log(f"[SSH] connecting to {SSH_USER}@{SSH_HOST}:{SSH_PORT} …")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: dict = dict(hostname=SSH_HOST, port=SSH_PORT, username=SSH_USER,
                        timeout=20, banner_timeout=30, auth_timeout=30)
        if SSH_KEY_PATH:
            kw["key_filename"] = SSH_KEY_PATH
        if SSH_PASSWORD:
            kw["password"] = SSH_PASSWORD

        client.connect(**kw)

        # Keep-alive: send a NOP every 30s
        transport = client.get_transport()
        transport.set_keepalive(30)

        self._ssh = client
        self._sftp = client.open_sftp()
        log("[SSH] connected ✓")

    def _close_quietly(self):
        try:
            if self._sftp:
                self._sftp.close()
        except Exception:
            pass
        try:
            if self._ssh:
                self._ssh.close()
        except Exception:
            pass
        self._ssh = None
        self._sftp = None

    def disconnect(self):
        self._close_quietly()
        log("[SSH] disconnected.")

    def ensure_connected(self):
        """Call before any remote operation."""
        try:
            if self._ssh and self._ssh.get_transport().is_active():
                return
        except Exception:
            pass
        self.connect()

    # ── raw exec ────────────────────────────────────────────────────────────

    @retried
    def exec(self, cmd: str, timeout: int = 30) -> tuple[str, str]:
        """Run a command; return (stdout, stderr). Raises on non-zero exit."""
        self.ensure_connected()
        _, stdout, stderr = self._ssh.exec_command(cmd, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        rc = stdout.channel.recv_exit_status()
        if rc != 0:
            raise RuntimeError(f"remote command exited {rc}: {cmd!r}\nstderr: {err.strip()}")
        return out, err

    def exec_nowait(self, cmd: str):
        """Fire-and-forget: don't wait for exit status (for tmux / bg jobs)."""
        self.ensure_connected()
        self._ssh.exec_command(cmd)

    # ── sftp ops ────────────────────────────────────────────────────────────

    @retried
    def sftp_put(self, local: str, remote: str):
        self.ensure_connected()
        self._sftp.put(local, remote)

    @retried
    def sftp_get(self, remote: str, local: str):
        self.ensure_connected()
        self._sftp.get(remote, local)

    @retried
    def sftp_stat(self, remote: str):
        self.ensure_connected()
        return self._sftp.stat(remote)

    @retried
    def sftp_remove(self, remote: str):
        self.ensure_connected()
        self._sftp.remove(remote)

    def sftp_exists(self, remote: str) -> bool:
        try:
            self.sftp_stat(remote)
            return True
        except (FileNotFoundError, IOError):
            return False

    @retried
    def sftp_read_text(self, remote: str) -> str:
        self.ensure_connected()
        with self._sftp.open(remote, "r") as f:
            return f.read().decode("utf-8", errors="replace")


# ══════════════════════════════════════════════════════════════════════════════
#  IGNORE PATTERNS
# ══════════════════════════════════════════════════════════════════════════════

def _compile_pattern(raw: str):
    p = raw.strip()
    if not p or p.startswith("#"):
        return None
    escaped = re.escape(p)
    escaped = escaped.replace(r"\*\*", "§DS§")
    escaped = escaped.replace(r"\*", "[^/]*")
    escaped = escaped.replace(r"\?", "[^/]")
    escaped = escaped.replace("§DS§", ".*")
    if not escaped.startswith("/"):
        escaped = r"(^|.*\/)" + escaped
    try:
        return re.compile(escaped + r"(/.*)?$")
    except re.error:
        return None


def load_ignore_patterns(root: Path) -> list:
    f = root / STIGNORE_FILE
    if not f.exists():
        return []
    patterns = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        c = _compile_pattern(line)
        if c:
            patterns.append(c)
    return patterns


def is_ignored(rel_path: str, patterns: list) -> bool:
    norm = rel_path.replace("\\", "/")
    return any(p.search(norm) for p in patterns)


# Build a shell `find` prune expression from ignore patterns
def _stignore_to_find_prunes(root: Path) -> str:
    """
    Emit a 'find ... ( <prune-expr> -prune ) -o ...' fragment.

    Handles three pattern shapes:
      ① simple name glob   e.g. *.jar, .DS_Store
          → -name "*.jar"
      ② **/name            e.g. **/node_modules, **/target
          → -name "node_modules"       (matches at any depth, cheapest)
      ③ **/path/segments   e.g. **/target/classes, **/build/generated
          → -path "*/target/classes"   (matches the full tail anywhere in tree)

    Patterns with a leading slash or other complex forms are skipped here and
    handled by the client-side is_ignored() filter that runs after the scan.
    """
    f = root / STIGNORE_FILE
    if not f.exists():
        return ""

    name_prunes: list[str] = []  # -name  "..."
    path_prunes: list[str] = []  # -path  "..."

    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("**/"):
            tail = line[3:]  # everything after the **/
            if not tail:
                continue
            if "/" not in tail:
                # ② **/name — simple name match at any depth
                name_prunes.append(f'-name "{tail}"')
            else:
                # ③ **/path/with/segments — use -path with a leading wildcard
                path_prunes.append(f'-path "*/{tail}"')
        elif "/" not in line:
            # ① bare glob (no separators) — name match
            name_prunes.append(f'-name "{line}"')
        elif line.startswith("*/"):
            # ③ path with segments but no leading **/ — use -path
            path_prunes.append(f'-path "{line}"')
        elif line.startswith("./"):
            # ③ path with segments but no leading **/ — use -path, ignore leading ./
            path_prunes.append(f'-path "*/{line[2:]}"')
        elif line.endswith("/**"):
            tail = line[:-3]  # remove trailing '/**'
            if tail.startswith("./"):
                tail = tail[2:]
            if tail:
                path_prunes.append(
                    f'-path "*/{tail}"')  # else: leading-slash absolute patterns, *.ext/sub, etc. — skip;
        #       is_ignored() handles them after the scan.

    path_prunes.append('-path "*/.git/*"')  # always ignore .git contents
    all_prunes = name_prunes + path_prunes
    if not all_prunes:
        return ""

    parts = r" -o ".join(r"\( " + p + r" -prune \)" for p in all_prunes)
    return r"\( " + parts + r" \) -o"


# ══════════════════════════════════════════════════════════════════════════════
#  ASYNC REMOTE SCAN  (tmux + find → temp file)
# ══════════════════════════════════════════════════════════════════════════════

# Output format:  <rel_path>\t<mtime_epoch>\t<size_bytes>\n
# "SCAN_DONE" is written as the last line so we know it's complete.

def start_remote_scan(mgr: SSHManager, patterns: list) -> str:
    """
    Fire a tmux/background `find` on the remote.
    Returns the path of the remote marker file (unique per run).
    The actual TSV is compressed to a .tsv.gz; a small `.done` marker
    is written when the gzip is complete so the client can poll the marker
    and then download & decompress the gz on the local side.
    """
    scan_id = uuid.uuid4().hex
    marker_file = f"{REMOTE_TMP}/sync_scan_{scan_id}.done"
    remote_gz = f"{REMOTE_TMP}/sync_scan_{scan_id}.tsv.gz"
    prune_expr = _stignore_to_find_prunes(LOCAL_ROOT)

    # find outputs: rel_path \t mtime_epoch \t size
    remote_root_str = str(REMOTE_ROOT)

    # Use nohup + sh so it survives even if our SSH channel drops.
    # Pipe through gzip to produce a compressed TSV, then write a small
    # marker file to indicate completion.
    find_cmd = (
        "nohup sh -c '"
        f"find {remote_root_str} {prune_expr} -type f "
        r'-printf "%P\t%T@\t%s\n" '
        "2>/dev/null "
        f'| gzip -c > "{remote_gz}" '
        f"&& echo SCAN_DONE > '{marker_file}'"
        "' >/dev/null 2>&1 &"
    )

    log(f"[scan] Firing remote scan → {remote_gz} (marker: {marker_file})")
    log(f"  find command: {find_cmd}")
    mgr.exec_nowait(find_cmd)
    return marker_file


def poll_remote_scan(mgr: SSHManager, marker_file: str,
                     poll_interval: int, poll_timeout: int) -> dict:
    """
    Poll the remote marker file until SCAN_DONE is present, then download
    the corresponding .tsv.gz, decompress it locally and parse the TSV.
    Returns the parsed scan dict.
    """
    import gzip

    deadline = time.monotonic() + poll_timeout
    dots = 0

    # Derive remote gz path from marker name
    if marker_file.endswith(".done"):
        remote_gz = marker_file[:-5] + ".tsv.gz"  # strip ".done", add ".tsv.gz"
    else:
        remote_gz = marker_file + ".tsv.gz"

    log(f"[scan] Polling for remote scan marker {marker_file} …")
    while time.monotonic() < deadline:
        try:
            if mgr.sftp_exists(marker_file):
                content = mgr.sftp_read_text(marker_file)
                if content.rstrip().endswith("SCAN_DONE"):
                    log(f"\n[scan] Remote scan complete.")
                    # Download compressed TSV and decompress locally for parsing
                    tmp_gz = Path(tempfile.mktemp(suffix=".tsv.gz"))
                    try:
                        mgr.sftp_get(remote_gz, str(tmp_gz))
                        with gzip.open(str(tmp_gz), "rt", encoding="utf-8", errors="replace") as gf:
                            tsv_text = gf.read()
                        return _parse_scan_output(tsv_text)
                    finally:
                        tmp_gz.unlink(missing_ok=True)
            print(".", end="", flush=True)
            dots += 1
            if dots % 20 == 0:
                elapsed = int(time.monotonic() - (deadline - poll_timeout))
                print(f" ({elapsed}s)", flush=True)
        except Exception as exc:
            vlog(f"  poll error (will retry): {exc}")
        time.sleep(poll_interval)

    raise TimeoutError(
        f"Remote scan did not finish within {poll_timeout}s. "
        f"Check {marker_file} (and corresponding .tsv.gz) on the remote manually."
    )


def _parse_scan_output(content: str) -> dict[str, tuple[float, int]]:
    """Parse `find -printf "%P\t%T@\t%s"` output."""
    result: dict[str, tuple[float, int]] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line == "SCAN_DONE":
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        rel_path, mtime_raw, size_raw = parts
        if not rel_path:
            continue
        try:
            result[rel_path] = (float(mtime_raw), int(size_raw))
        except ValueError:
            continue
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  LOCAL SCAN
# ══════════════════════════════════════════════════════════════════════════════

def local_list_all(root: Path, patterns: list) -> dict[str, tuple[float, int]]:
    """Returns {rel_posix: (mtime, size)}"""
    result: dict[str, tuple[float, int]] = {}
    skip_names = {STATE_FILE.name, PROGRESS_FILE.name, STIGNORE_FILE}
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if p.name in skip_names and p.parent == root:
            continue
        # Skip conflict artefacts
        if ".conflict" in p.name:
            continue
        rel = p.relative_to(root).as_posix()
        if is_ignored(rel, patterns):
            continue
        st = p.stat()
        result[rel] = (st.st_mtime, st.st_size)
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  STATE FILE  (persistent across runs — records last-synced mtime+size)
# ══════════════════════════════════════════════════════════════════════════════

def load_state() -> dict:
    """
    Format: {rel_path: {"lmtime": f, "lsize": i, "rmtime": f, "rsize": i}}
    """
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True), "utf-8")


# ══════════════════════════════════════════════════════════════════════════════
#  PROGRESS FILE  (checkpoint for current sync run — enables resume)
# ══════════════════════════════════════════════════════════════════════════════

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
    PROGRESS_FILE.write_text(json.dumps(prog, indent=2), "utf-8")


def clear_progress():
    if PROGRESS_FILE.exists():
        PROGRESS_FILE.unlink()


# ══════════════════════════════════════════════════════════════════════════════
#  BATCH TRANSFER HELPERS
# ═════════════════════════════════════════════════════════════════════════════=

def push_batch(mgr: SSHManager, files: list[tuple[str, Path]],
               dry_run: bool, state: dict, prog: dict):
    """
    Push a list of (rel_path, local_Path) to remote as one tar.gz.
    Each successfully transferred file is immediately checkpointed.
    """
    if not files:
        return
    if dry_run:
        for rel, _ in files:
            log(f"  [PUSH-DRY] {rel}")
        return

    tmp_tar = Path(tempfile.mktemp(suffix=".tar.gz"))
    remote_tar = f"{REMOTE_TMP}/sync_push_{uuid.uuid4().hex}.tar.gz"

    try:
        # ── Pack ────────────────────────────────────────────────────────────
        log(f"  [PUSH] packing {len(files)} file(s) into {tmp_tar.name} …")
        with tarfile.open(tmp_tar, "w:gz", compresslevel=6) as tar:
            for rel, lpath in files:
                tar.add(str(lpath), arcname=rel)
        size_kb = tmp_tar.stat().st_size // 1024
        log(f"  [PUSH] packed → {size_kb} KB")

        # ── Upload ──────────────────────────────────────────────────────────
        log(f"  [PUSH] uploading {tmp_tar.name} → remote …")
        mgr.sftp_put(str(tmp_tar), remote_tar)

        # ── Extract on remote ───────────────────────────────────────────────
        extract_cmd = (
            f"cd '{REMOTE_ROOT}' && "
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

    finally:
        tmp_tar.unlink(missing_ok=True)
        try:
            mgr.sftp_remove(remote_tar)
        except Exception:
            pass


def pull_batch(mgr: SSHManager, files: list[str],
               dry_run: bool, state: dict, prog: dict,
               remote_meta: dict[str, tuple[float, int]]):
    """
    Pull a list of rel_paths from remote as one tar.gz.
    """
    if not files:
        return
    if dry_run:
        for rel in files:
            log(f"  [PULL-DRY] {rel}")
        return

    remote_tar = f"{REMOTE_TMP}/sync_pull_{uuid.uuid4().hex}.tar.gz"
    tmp_tar = Path(tempfile.mktemp(suffix=".tar.gz"))

    try:
        # ── Pack on remote ──────────────────────────────────────────────────
        # Build a null-delimited file list to avoid shell quoting issues
        file_list_remote = f"{REMOTE_TMP}/sync_filelist_{uuid.uuid4().hex}.txt"
        file_list_content = "\n".join(files)

        log(f"  [PULL] requesting remote to pack {len(files)} file(s) …")

        # Write file list to remote via echo (chunked to avoid ARG_MAX)
        _write_remote_file(mgr, file_list_remote, file_list_content)

        pack_cmd = (
            f"cd '{REMOTE_ROOT}' && "
            f"tar czf '{remote_tar}' --no-recursion -T '{file_list_remote}' "
            f"--ignore-failed-read 2>&1"
        )
        mgr.exec(pack_cmd, timeout=120)

        # ── Download ────────────────────────────────────────────────────────
        log(f"  [PULL] downloading …")
        mgr.sftp_get(remote_tar, str(tmp_tar))
        size_kb = tmp_tar.stat().st_size // 1024
        log(f"  [PULL] downloaded {size_kb} KB, extracting …")

        # ── Extract locally ─────────────────────────────────────────────────
        with tarfile.open(tmp_tar, "r:gz") as tar:
            for member in tar.getmembers():
                dest = LOCAL_ROOT / member.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as src_f, open(dest, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                # Restore remote mtime
                if member.name in remote_meta:
                    rmt = int(remote_meta[member.name][0])
                    os.utime(dest, (rmt, rmt))

        # ── Checkpoint ──────────────────────────────────────────────────────
        for rel in files:
            lpath = LOCAL_ROOT / rel
            if lpath.exists():
                st = lpath.stat()
                rmtime, rsize = remote_meta.get(rel, (st.st_mtime, st.st_size))
                state[rel] = {
                    "lmtime": st.st_mtime, "lsize": st.st_size,
                    "rmtime": rmtime, "rsize": rsize,
                }
                prog["pulled"].append(rel)
                log(f"  [PULL ✓] {rel}")
        save_progress(prog)
        save_state(state)

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
    import base64
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    cmd = f"echo '{b64}' | base64 -d > '{remote_path}'"
    mgr.exec(cmd, timeout=30)


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


# ══════════════════════════════════════════════════════════════════════════════
#  CONFLICT HANDLING
# ══════════════════════════════════════════════════════════════════════════════

import hashlib


def _md5_local(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _md5_remote(mgr: SSHManager, remote_path: str) -> str:
    """Return MD5 hex digest of a remote file using md5sum."""
    out, _ = mgr.exec(f"md5sum '{remote_path}'", timeout=30)
    # md5sum output: "<hash>  <filename>"
    return out.strip().split()[0]


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


# ══════════════════════════════════════════════════════════════════════════════
#  DECISION ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def _file_changed(new_mtime: float, new_size: int,
                  old_mtime: Optional[float], old_size: Optional[int]) -> bool:
    """True if the file is different from what we last recorded."""
    if old_mtime is None:
        return True
    mtime_diff = abs(new_mtime - old_mtime)
    return mtime_diff > MTIME_TOLERANCE or new_size != old_size


def decide(local_files: dict[str, tuple[float, int]],
           remote_files: dict[str, tuple[float, int]],
           state: dict,
           progress: dict,
           push_only: bool,
           pull_only: bool) -> dict:
    """
    Returns a plan:
    {
      "to_push":     [(rel, local_path), …],
      "to_pull":     [rel, …],
      "to_delete_r": [rel, …],   # delete from remote
      "to_delete_l": [rel, …],   # delete locally
      "conflicts":   [rel, …],
    }
    Files already in progress (checkpointed) are skipped.
    """
    done_push = set(progress.get("pushed", []))
    done_pull = set(progress.get("pulled", []))
    done_del_r = set(progress.get("deleted_r", []))
    done_del_l = set(progress.get("deleted_l", []))

    # conflicts entries are (rel, reason_str) tuples
    plan = dict(to_push=[], to_pull=[], to_delete_r=[], to_delete_l=[],
                conflicts=[])

    all_keys = set(local_files) | set(remote_files)

    for rel in sorted(all_keys):
        # Already handled in a previous (failed) attempt?
        if rel in done_push or rel in done_pull:
            vlog(f"  [RESUME-SKIP] {rel}")
            continue

        l_meta = local_files.get(rel)  # (mtime, size) or None
        r_meta = remote_files.get(rel)
        prev = state.get(rel, {})

        prev_lmtime: Optional[float] = prev.get("lmtime")
        prev_lsize: Optional[int] = prev.get("lsize")
        prev_rmtime: Optional[float] = prev.get("rmtime")
        prev_rsize: Optional[int] = prev.get("rsize")

        # ── Only local ───────────────────────────────────────────────────────
        if l_meta and not r_meta:
            if prev_rmtime is not None and rel not in done_del_l:
                # Was synced before, now missing on remote → remote deleted it
                if not pull_only:
                    plan["to_delete_l"].append(rel)
            else:
                # New local file
                if not pull_only and rel not in done_push:
                    plan["to_push"].append((rel, LOCAL_ROOT / rel))
            continue

        # ── Only remote ──────────────────────────────────────────────────────
        if r_meta and not l_meta:
            if prev_lmtime is not None and rel not in done_del_r:
                # Was synced before, now missing locally → local deleted it
                if not push_only:
                    plan["to_delete_r"].append(rel)
            else:
                # New remote file
                if not push_only and rel not in done_pull:
                    plan["to_pull"].append(rel)
            continue

        # ── Both sides ───────────────────────────────────────────────────────
        l_mtime, l_size = l_meta
        r_mtime, r_size = r_meta

        l_changed = _file_changed(l_mtime, l_size, prev_lmtime, prev_lsize)
        r_changed = _file_changed(r_mtime, r_size, prev_rmtime, prev_rsize)

        if not l_changed and not r_changed:
            vlog(f"  [SKIP] {rel}")
            continue

        if l_changed and r_changed:
            # Both changed — check if they're actually the same size+mtime
            # (can happen on first run with matching files)
            if abs(l_mtime - r_mtime) <= MTIME_TOLERANCE and l_size == r_size:
                vlog(f"  [SKIP-SAME] {rel}")
                # Record as synced so we don't revisit
                state[rel] = {
                    "lmtime": l_mtime, "lsize": l_size,
                    "rmtime": r_mtime, "rsize": r_size,
                }
                continue
            # Build a human-readable reason for the conflict-info file
            reason_parts = []
            if prev_lmtime is None:
                reason_parts.append("file was never synced before (first-run conflict)")
            else:
                lmtime_diff = abs(l_mtime - prev_lmtime)
                rmtime_diff = abs(r_mtime - prev_rmtime) if prev_rmtime is not None else None
                if lmtime_diff > MTIME_TOLERANCE or l_size != prev_lsize:
                    reason_parts.append(
                        f"local changed (mtime Δ={lmtime_diff:.0f}s, "
                        f"size {prev_lsize}→{l_size})"
                    )
                if rmtime_diff is not None and (rmtime_diff > MTIME_TOLERANCE or r_size != prev_rsize):
                    reason_parts.append(
                        f"remote changed (mtime Δ={rmtime_diff:.0f}s, "
                        f"size {prev_rsize}→{r_size})"
                    )
            reason = "; ".join(reason_parts) if reason_parts else "both sides changed since last sync"
            plan["conflicts"].append((rel, reason))
            continue

        if l_changed and not pull_only:
            plan["to_push"].append((rel, LOCAL_ROOT / rel))
        elif r_changed and not push_only:
            plan["to_pull"].append(rel)

    return plan


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

BATCH_SIZE = 50  # max files per tar batch


def run_sync(dry_run=False, verbose=False, force=False,
             push_only=False, pull_only=False,
             poll_interval=5, poll_timeout=120):
    global _verbose
    _verbose = verbose

    print(f"\n{'=' * 64}")
    print(f"  Sync  {LOCAL_ROOT}")
    print(f"   ↔   {SSH_USER}@{SSH_HOST}:{SSH_PORT}:{REMOTE_ROOT}")
    print(f"{'=' * 64}")
    if dry_run:
        print("  *** DRY-RUN — no files will be changed ***")
    print()

    patterns = load_ignore_patterns(LOCAL_ROOT)
    log(f"[ignore] {len(patterns)} pattern(s) loaded from {STIGNORE_FILE}")

    # ── Pre-flight: check for leftover conflict files ──────────────────────
    if not check_existing_conflicts(dry_run):
        return

    state = {} if force else load_state()
    progress = {} if force else load_progress()

    if progress and not force:
        pushed_n = len(progress.get("pushed", []))
        pulled_n = len(progress.get("pulled", []))
        if pushed_n or pulled_n:
            log(f"[resume] Resuming previous session "
                f"(already pushed={pushed_n}, pulled={pulled_n})")

    # ── Start SSH ─────────────────────────────────────────────────────────────
    mgr = SSHManager()
    mgr.connect()

    scan_file = None
    try:
        # ── 1. Fire remote scan (async — runs on server) ───────────────────
        scan_file = start_remote_scan(mgr, patterns)

        # ── 2. Do local scan while remote is running ───────────────────────
        log("[scan] Scanning local files …")
        local_files = local_list_all(LOCAL_ROOT, patterns)
        log(f"[scan] {len(local_files)} local file(s) found")

        # ── 3. Wait for remote scan ─────────────────────────────────────────
        log(f"[scan] Waiting for remote scan (poll every {poll_interval}s, "
            f"timeout {poll_timeout}s) …")
        remote_files_raw = poll_remote_scan(mgr, scan_file,
                                            poll_interval, poll_timeout)

        # Apply client-side ignore filter (catches complex patterns find didn't prune)
        remote_files: dict[str, tuple[float, int]] = {
            rel: meta
            for rel, meta in remote_files_raw.items()
            if not is_ignored(rel, patterns)
        }
        log(f"[scan] {len(remote_files)} remote file(s) after filtering")

        # ── 4. Decide what to do ────────────────────────────────────────────
        plan = decide(local_files, remote_files, state, progress,
                      push_only, pull_only)

        # Exclude .git entries from deletion plans so they are not counted or acted on.
        filtered_del_r = [
            r for r in plan["to_delete_r"]
            if "/.git/" not in r and not r.endswith("/.git")
        ]
        filtered_del_l = [
            r for r in plan["to_delete_l"]
            if "/.git/" not in r and not r.endswith("/.git")
        ]

        n_push = len(plan["to_push"])
        n_pull = len(plan["to_pull"])
        n_del_r = len(filtered_del_r)
        n_del_l = len(filtered_del_l)
        n_conf = len(plan["conflicts"])
        n_total = n_push + n_pull + n_del_r + n_del_l + n_conf

        log(f"[plan] push={n_push}  pull={n_pull}  "
            f"del_remote={n_del_r}  del_local={n_del_l}  "
            f"conflicts={n_conf}  (total={n_total})")

        if n_total == 0:
            log("[sync] Nothing to do — already in sync ✓")
            clear_progress()
            return

        print()

        # ── 5. Execute: push in batches ─────────────────────────────────────
        if plan["to_push"]:
            log(f"[push] Pushing {n_push} file(s) in batches of {BATCH_SIZE} …")
            for i in range(0, n_push, BATCH_SIZE):
                batch = plan["to_push"][i:i + BATCH_SIZE]
                log(f"[push] Batch {i // BATCH_SIZE + 1}: {len(batch)} file(s)")
                push_batch(mgr, batch, dry_run, state, progress)

        # ── 6. Execute: pull in batches ─────────────────────────────────────
        if plan["to_pull"]:
            log(f"[pull] Pulling {n_pull} file(s) in batches of {BATCH_SIZE} …")
            for i in range(0, n_pull, BATCH_SIZE):
                batch = plan["to_pull"][i:i + BATCH_SIZE]
                log(f"[pull] Batch {i // BATCH_SIZE + 1}: {len(batch)} file(s)")
                pull_batch(mgr, batch, dry_run, state, progress, remote_files)

        # ── 7. Deletions ────────────────────────────────────────────────────
        if filtered_del_r:
            log(f"[del] Deleting {n_del_r} file(s) from remote …")
            delete_remote(mgr, filtered_del_r, dry_run, state, progress)

        if filtered_del_l and not dry_run:
            confirmed_local = _confirm_deletions_by_leaf(filtered_del_l, context="local")
            if confirmed_local is None:
                log("Local deletions skipped by user.")
            else:
                for rel in confirmed_local:
                    lpath = LOCAL_ROOT / rel
                    lpath.unlink(missing_ok=True)
                    state.pop(rel, None)
                    progress.setdefault("deleted_l", []).append(rel)
                    log(f"  [DEL-LOCAL ✓] {rel}")
                save_state(state)
                save_progress(progress)
        elif filtered_del_l and dry_run:
            for rel in filtered_del_l:
                log(f"  [DEL-LOCAL-DRY] {rel}")

        # ── 8. Conflicts ───────────────────────────────────────────────────
        if plan["conflicts"]:
            log(f"[conflict] Handling {n_conf} conflict(s) …")
            for rel, reason in plan["conflicts"]:
                lpath = LOCAL_ROOT / rel
                remote_path = str(REMOTE_ROOT / rel)
                save_conflict(mgr, rel, lpath, remote_path, dry_run, reason)

        # ── 9. Final state save + clear progress ───────────────────────────
        if not dry_run:
            save_state(state)
            clear_progress()

        print()
        print(f"{'─' * 64}")
        print(" SUMMARY")
        print(f"  Pushed     : {n_push}")
        print(f"  Pulled     : {n_pull}")
        print(f"  Del remote : {n_del_r}")
        print(f"  Del local  : {n_del_l}")
        print(f"  Conflicts  : {n_conf}")
        print(f"{'─' * 64}")

        if n_conf:
            print()
            print("⚠  CONFLICTS — look for *.conflict files in your local tree.")
            print("   Merge manually in IntelliJ → Git → Resolve Conflicts,")
            print("   delete the .conflict* files, then run sync again.")

    except KeyboardInterrupt:
        print()
        warn("Interrupted by user. Progress saved — next run will resume.")
        if not dry_run:
            save_state(state)
            save_progress(progress)

    except Exception as exc:
        warn(f"Sync failed: {exc}")
        if verbose:
            traceback.print_exc()
        if not dry_run:
            save_state(state)
            save_progress(progress)
        warn("Progress saved — next run will resume from last checkpoint.")
        sys.exit(1)

    finally:
        # Clean up remote scan file
        if scan_file:
            try:
                mgr.sftp_remove(scan_file)
                vlog(f"[cleanup] removed remote scan file {scan_file}")
            except Exception:
                pass
        mgr.disconnect()


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Unstable-connection-tolerant bidirectional SSH sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="Preview without applying changes")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show every file, not just actions")
    parser.add_argument("-f", "--force", action="store_true",
                        help="Ignore state+progress cache (full rescan)")
    parser.add_argument("--push-only", action="store_true",
                        help="Only local→remote")
    parser.add_argument("--pull-only", action="store_true",
                        help="Only remote→local")
    parser.add_argument("--poll-interval", type=int, default=5,
                        metavar="N",
                        help="Seconds between remote-scan polls (default: 5)")
    parser.add_argument("--poll-timeout", type=int, default=120,
                        metavar="N",
                        help="Max seconds to wait for remote scan (default: 120)")
    args = parser.parse_args()

    if args.push_only and args.pull_only:
        parser.error("--push-only and --pull-only are mutually exclusive")

    run_sync(
        dry_run=args.dry_run,
        verbose=args.verbose,
        force=args.force,
        push_only=args.push_only,
        pull_only=args.pull_only,
        poll_interval=args.poll_interval,
        poll_timeout=args.poll_timeout,
    )


if __name__ == "__main__":
    main()
