"""
Copilot command operations for syncript
Author: Younes Rahimi

Runs the `copilot` CLI on the remote server asynchronously (nohup),
streams the log file back in real-time, and supports session management.
"""
import re
import sys
import time
import uuid
from pathlib import Path, PurePosixPath

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)

from . import config as _cfg
from .core.ssh_manager import SSHManager
from .utils.logging import log, warn

REMOTE_LOGS_DIR = "~/.syncript/logs"
LOG_RETENTION_DAYS = 30
DEFAULT_MODEL = "claude-sonnet-4.6"
STREAM_POLL_INTERVAL = 1     # seconds between log polls
RECONNECT_WAIT = 5           # seconds before reconnect attempt


# ── helpers ──────────────────────────────────────────────────────────────────

def _resolve_remote_cwd() -> str:
    """
    Return the remote working directory that corresponds to the local cwd.
    Computes cwd relative to LOCAL_ROOT and appends it to REMOTE_ROOT.
    Falls back to REMOTE_ROOT if cwd is outside LOCAL_ROOT.
    """
    try:
        local_cwd = Path.cwd().resolve()
        local_root = Path(_cfg.LOCAL_ROOT).resolve()
        rel = local_cwd.relative_to(local_root)
        return str(PurePosixPath(_cfg.REMOTE_ROOT) / rel.as_posix())
    except ValueError:
        return str(_cfg.REMOTE_ROOT)


def _log_path(session_id: str, folder_name: str = "", timestamp: str = "") -> str:
    parts = ["copilot"]
    if folder_name:
        parts.append(folder_name)
    parts.append(session_id)
    if timestamp:
        parts.append(timestamp)
    return f"{REMOTE_LOGS_DIR}/{'-'.join(parts)}.log"


def _find_log_by_session_id(ssh: SSHManager, session_id: str) -> str:
    """Locate a log file by session UUID on the remote server."""
    out, _ = ssh.exec(
        f"ls {REMOTE_LOGS_DIR}/copilot-*{session_id}*.log 2>/dev/null || true",
        timeout=15,
    )
    files = [f.strip() for f in out.strip().splitlines() if f.strip()]
    if not files:
        raise FileNotFoundError(f"No log file found for session {session_id}")
    return files[0]


def _ensure_logs_dir(ssh: SSHManager):
    """Create the remote logs directory if it does not exist."""
    ssh.exec(f"mkdir -p {REMOTE_LOGS_DIR}", timeout=15)


def _cleanup_old_logs(ssh: SSHManager):
    """Delete log files older than LOG_RETENTION_DAYS on the remote server."""
    try:
        ssh.exec(
            f"find {REMOTE_LOGS_DIR} -name 'copilot-*.log' "
            f"-mtime +{LOG_RETENTION_DAYS} -delete",
            timeout=15,
        )
    except Exception as exc:
        warn(f"[copilot] log cleanup warning: {exc}")


def _stream_log(ssh: SSHManager, log_file: str, start_offset: int = 0) -> int:
    """
    Stream *log_file* from *start_offset* until the remote process finishes.
    Returns the final byte offset reached (for resume after reconnect).
    Uses a sentinel written to the log file by the wrapper script to detect completion.
    """
    offset = start_offset
    while True:
        try:
            ssh.ensure_connected()
            # Read new bytes from offset
            out, _ = ssh.exec(
                f"tail -c +{offset + 1} {log_file} 2>/dev/null || true",
                timeout=30,
            )
            if out:
                sys.stdout.write(out)
                sys.stdout.flush()
                offset += len(out.encode("utf-8", errors="replace"))

            # Check if the sentinel line exists (process finished)
            done_out, _ = ssh.exec(
                f"grep -c '__COPILOT_DONE__' {log_file} 2>/dev/null || echo 0",
                timeout=10,
            )
            if done_out.strip() not in ("", "0"):
                break

        except KeyboardInterrupt:
            print("\n[copilot] interrupted (remote process is still running).")
            break
        except Exception as exc:
            warn(f"[copilot] connection lost ({exc}); reconnecting in {RECONNECT_WAIT}s …")
            time.sleep(RECONNECT_WAIT)
            try:
                ssh.connect()
            except Exception:
                pass
            continue

        time.sleep(STREAM_POLL_INTERVAL)

    return offset


# ── public commands ───────────────────────────────────────────────────────────

def run_copilot(extra_args: list, model=None, autopilot: bool = False, verbose: bool = False):
    """
    Execute `copilot` on the remote server asynchronously and stream its output.

    The remote command is wrapped in a small shell script that:
      1. Changes to the correct working directory.
      2. Runs copilot via nohup.
      3. Writes a sentinel line when done so the streamer can stop.
    """
    syncript_path = _find_config()
    _apply_config(syncript_path, verbose)

    session_id = str(uuid.uuid4())
    folder_name = Path.cwd().name
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    log_file = _log_path(session_id, folder_name=folder_name, timestamp=timestamp)
    remote_cwd = _resolve_remote_cwd()
    effective_model = model or DEFAULT_MODEL

    # Build copilot args, ensuring --model and required flags are present
    copilot_args = list(extra_args or [])
    if "--model" not in copilot_args:
        copilot_args.extend(["--model", effective_model])
    # Remove --yolo / --share if user accidentally passed them (we control these)
    for flag in ("--yolo", "--share"):
        if flag in copilot_args:
            copilot_args.remove(flag)

    autopilot_flag = "--autopilot " if autopilot else ""
    copilot_cmd = (
        f"copilot --yolo {autopilot_flag}"
        f'-p "Read \'.copilot.prompt.md\' file for the actual prompt" '
        f"--share {log_file} "
        + " ".join(copilot_args)
    )

    # Wrapper: cd, nohup copilot, append sentinel when done
    wrapper = (
        f"mkdir -p {REMOTE_LOGS_DIR} && "
        f"cd {remote_cwd} && "
        f"nohup bash -c '{copilot_cmd} >> {log_file} 2>&1 ; "
        f"echo __COPILOT_DONE__ >> {log_file}' "
        f"> /dev/null 2>&1 & echo $!"
    )

    ssh = SSHManager()
    ssh.connect()
    _ensure_logs_dir(ssh)
    _cleanup_old_logs(ssh)

    # Print the command being executed on the remote server
    print(f"[copilot] executing on remote server:\n  {copilot_cmd}\n")

    # Launch the remote process exactly once — never retry, as a duplicate
    # invocation could cause unintended consequences on the remote server.
    try:
        pid_out, _ = ssh.exec_once(wrapper, timeout=30)
        pid = pid_out.strip()
        log(f"[copilot] session {session_id} started (remote PID {pid})")
    except Exception as exc:
        warn(f"[copilot] failed to launch on remote server: {exc}")
        warn(f"[copilot] NOT retrying. Session ID: {session_id}")
        warn(f"[copilot] If the process did start, resume with: syncript copilot resume {session_id}")
        ssh.disconnect()
        return
    log(f"[copilot] log file : {log_file}")
    print(f"--- copilot session {session_id} ---")

    # Give the process a moment to start writing
    time.sleep(1.0)

    # Stream output until done
    _stream_log(ssh, log_file)

    ssh.disconnect()


def list_logs(verbose: bool = False):
    """List all copilot log files on the remote server."""
    syncript_path = _find_config()
    _apply_config(syncript_path, verbose)

    ssh = SSHManager()
    ssh.connect()

    try:
        out, _ = ssh.exec(
            f"ls -lt --time-style=+\"%Y-%m-%d %H:%M:%S\" {REMOTE_LOGS_DIR}/copilot-*.log 2>/dev/null || true",
            timeout=15,
        )
    finally:
        ssh.disconnect()

    if not out.strip():
        print("No copilot log files found.")
        return

    print(f"{'SESSION ID':<38}  {'FOLDER':<20}  {'MODIFIED':<19}  {'SIZE':>8}")
    print("-" * 92)
    for line in out.strip().splitlines():
        # ls -lt output: perms links user group size date time path
        parts = line.split()
        if len(parts) < 8:
            continue
        size = parts[4]
        date = parts[5]
        time_str = parts[6]
        path = parts[-1]
        fname = path.split("/")[-1]
        if fname.startswith("copilot-") and fname.endswith(".log"):
            m = _UUID_RE.search(fname)
            if m:
                session_id = m.group(0)
                # folder name is between "copilot-" and the UUID
                prefix = fname[len("copilot-"):m.start()]
                folder = prefix.rstrip("-") if prefix else ""
                print(f"{session_id:<38}  {folder:<20}  {date} {time_str}  {size:>8}")


def view_log(session_id: str, verbose: bool = False):
    """Print the full contents of a copilot session log."""
    syncript_path = _find_config()
    _apply_config(syncript_path, verbose)

    ssh = SSHManager()
    ssh.connect()

    try:
        try:
            log_file = _find_log_by_session_id(ssh, session_id)
        except FileNotFoundError:
            print(f"No log found for session {session_id}.")
            return
        out, _ = ssh.exec(f"cat {log_file} 2>/dev/null || true", timeout=30)
    finally:
        ssh.disconnect()

    if not out.strip():
        print(f"No log found for session {session_id}.")
    else:
        sys.stdout.write(out)


def stop_copilot(session_id: str, verbose: bool = False):
    """Terminate a running copilot session on the remote server."""
    syncript_path = _find_config()
    _apply_config(syncript_path, verbose)

    ssh = SSHManager()
    ssh.connect()

    try:
        try:
            log_file = _find_log_by_session_id(ssh, session_id)
        except FileNotFoundError:
            print(f"No running copilot process found for session {session_id}.")
            return
        out, _ = ssh.exec(
            f"pgrep -f {log_file} 2>/dev/null || true",
            timeout=15,
        )
        pids = out.strip().splitlines()
        if not pids:
            print(f"No running copilot process found for session {session_id}.")
        else:
            for pid in pids:
                try:
                    ssh.exec(f"kill {pid.strip()}", timeout=10)
                    log(f"[copilot] sent SIGTERM to PID {pid.strip()}")
                except Exception:
                    pass
            # Append a note to the log
            ssh.exec(
                f"echo '\n[syncript] session stopped by user' >> {log_file} ; "
                f"echo __COPILOT_DONE__ >> {log_file}",
                timeout=10,
            )
            print(f"Stopped copilot session {session_id}.")
    finally:
        ssh.disconnect()


def resume_copilot(session_id: str, verbose: bool = False):
    """Resume streaming an existing copilot session log from the beginning."""
    syncript_path = _find_config()
    _apply_config(syncript_path, verbose)

    ssh = SSHManager()
    ssh.connect()

    try:
        log_file = _find_log_by_session_id(ssh, session_id)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        ssh.disconnect()
        return

    log(f"[copilot] resuming session {session_id}")
    log(f"[copilot] log file : {log_file}")
    print(f"--- copilot session {session_id} (resumed) ---")

    _stream_log(ssh, log_file)
    ssh.disconnect()


# ── internal helpers ──────────────────────────────────────────────────────────

def _find_config():
    from .config import find_syncript
    path = find_syncript()
    if path is None:
        print("error: no .syncript file found. Run 'syncript init' first.", file=sys.stderr)
        sys.exit(1)
    return path


def _apply_config(syncript_path, verbose: bool):
    from .config import load_syncript_file, get_profile, apply_profile
    if verbose:
        log(f"[config] Using {syncript_path}")
    data = load_syncript_file(syncript_path)
    profile = get_profile(data, "default")
    apply_profile(profile)
