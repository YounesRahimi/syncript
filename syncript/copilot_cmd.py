"""
Copilot command operations for syncript
Author: Younes Rahimi

Runs the `copilot` CLI on the remote server asynchronously (nohup),
streams the log file back in real-time, and supports session management.
"""
import re
import shutil
import subprocess
import sys
import time
import uuid

try:
    import termios
    import tty
    _HAS_TERMIOS = True
except ImportError:
    _HAS_TERMIOS = False
from pathlib import Path, PurePosixPath

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)

from . import config as _cfg
from .core.ssh_manager import SSHManager
from .utils.logging import log, warn

REMOTE_LOGS_DIR = "~/.syncript/logs"
_FNAME_TS_RE = re.compile(r"(\d{8})-(\d{6})\.log$")
LOG_RETENTION_DAYS = 30
DEFAULT_MODEL = "claude-sonnet-4.6"
STREAM_POLL_INTERVAL = 10    # seconds between log polls
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


def _transfer_prompt_file(ssh: SSHManager, remote_cwd: str):
    """Upload .copilot.prompt.md from the local cwd to the remote cwd, if present."""
    local_prompt = Path.cwd() / ".copilot.prompt.md"
    if not local_prompt.exists():
        return
    remote_prompt = f"{remote_cwd}/.copilot.prompt.md"
    try:
        ssh.exec(f"mkdir -p {remote_cwd}", timeout=15)
        ssh.sftp_put(str(local_prompt), remote_prompt)
        log(f"[copilot] transferred {local_prompt.name} → {remote_prompt}")
    except Exception as exc:
        warn(f"[copilot] could not transfer .copilot.prompt.md: {exc}")


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


# ── interactive log browser helpers ──────────────────────────────────────────

def _parse_log_timestamp(fname: str) -> str:
    """Extract timestamp from log filename and return as 'YYYY-MM-DD HH:MM:SS'."""
    m = _FNAME_TS_RE.search(fname)
    if m:
        d, t = m.group(1), m.group(2)
        return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}:{t[4:6]}"
    return "(unknown)"


def _clear_screen():
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _page_content(content: str):
    """Display *content* through a pager (less or more) when in a TTY, else write directly."""
    pager = shutil.which("less") or shutil.which("more")
    if pager and sys.stdout.isatty():
        subprocess.run([pager, "-R"], input=content.encode(), check=False)
    else:
        sys.stdout.write(content)


def _getch() -> str:
    """Read one raw character from stdin."""
    if not _HAS_TERMIOS:
        import msvcrt
        return msvcrt.getwch()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _display_log_list(entries: list):
    """Render the numbered log list to the terminal."""
    _clear_screen()
    col_id = 38
    col_folder = 22
    col_ts = 21
    header = f"  {'#':<4}{'SESSION ID':<{col_id}}{'FOLDER':<{col_folder}}{'STARTED':<{col_ts}}"
    print(header)
    print("  " + "-" * (col_id + col_folder + col_ts + 4))
    for i, e in enumerate(entries, 1):
        folder = e["folder"][:col_folder - 1] if e["folder"] else ""
        print(f"  {i:<4}{e['session_id']:<{col_id}}{folder:<{col_folder}}{e['timestamp']:<{col_ts}}")
    print()


def _read_selection(max_n: int) -> "int | None":
    """
    Prompt for a 1-based index selection.
    Returns the chosen index or None if Esc / Ctrl+C / 'q' was pressed.
    """
    prompt = f"Select log [1-{max_n}], or press Esc/Ctrl+C to exit: "
    sys.stdout.write(prompt)
    sys.stdout.flush()

    if not _HAS_TERMIOS:
        import msvcrt
        buf = ""
        while True:
            ch = msvcrt.getwch()
            if ch in ("\x1b", "\x03", "q", "Q"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return None
            elif ch in ("\r", "\n"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                if buf.isdigit():
                    n = int(buf)
                    if 1 <= n <= max_n:
                        return n
                buf = ""
                sys.stdout.write(prompt)
                sys.stdout.flush()
            elif ch in ("\x7f", "\x08"):
                if buf:
                    buf = buf[:-1]
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            elif ch.isdigit():
                buf += ch
                sys.stdout.write(ch)
                sys.stdout.flush()
        return None

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    buf = ""
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch in ("\x1b", "\x03", "q", "Q"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                return None
            elif ch in ("\r", "\n"):
                sys.stdout.write("\r\n")
                sys.stdout.flush()
                if buf.isdigit():
                    n = int(buf)
                    if 1 <= n <= max_n:
                        return n
                # Invalid input – redisplay prompt
                buf = ""
                sys.stdout.write(prompt)
                sys.stdout.flush()
            elif ch in ("\x7f", "\x08"):  # backspace
                if buf:
                    buf = buf[:-1]
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
            elif ch.isdigit():
                buf += ch
                sys.stdout.write(ch)
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _display_log_content(entry: dict, content: str):
    """
    Display the full log file content via a pager.
    When the user exits the pager (e.g. 'q' in less), control returns to the
    interactive log list.
    """
    header = (
        f"Session : {entry['session_id']}\n"
        f"Folder  : {entry['folder']}\n"
        f"Started : {entry['timestamp']}\n"
        + "-" * 72 + "\n"
    )
    full_content = header + (content if content.strip() else "(log is empty)\n")
    _page_content(full_content)



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

    # Escape single quotes in copilot_cmd so it can be safely embedded inside
    # the outer bash -c '...' single-quoted string.  The sequence '\'' ends the
    # single-quoted string, appends a literal single quote, and re-opens it.
    escaped_cmd = copilot_cmd.replace("'", "'\\''")

    # Wrapper: cd, nohup copilot in the background, append sentinel when done.
    # The trailing "& echo $!" makes the SSH command return immediately with the
    # remote PID instead of blocking until copilot finishes (which can take minutes).
    wrapper = (
        f"mkdir -p {REMOTE_LOGS_DIR} && "
        f"cd {remote_cwd} && "
        f"nohup bash -c '{escaped_cmd} >> {log_file} 2>&1 ; "
        f"echo __COPILOT_DONE__ >> {log_file}' "
        f"> /dev/null 2>&1 & echo $!"
    )


    ssh = SSHManager()
    ssh.connect()
    _ensure_logs_dir(ssh)
    _cleanup_old_logs(ssh)

    # Transfer .copilot.prompt.md to remote cwd if it exists locally
    _transfer_prompt_file(ssh, remote_cwd)

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
    """
    Interactive copilot log browser.

    Displays a numbered list of available log files. The user can select a log
    by number to view its last 50 lines, then press Esc to return to the list.
    Falls back to a plain listing when stdout is not a TTY.
    """
    syncript_path = _find_config()
    _apply_config(syncript_path, verbose)

    ssh = SSHManager()
    ssh.connect()

    try:
        out, _ = ssh.exec(
            f"ls -1t {REMOTE_LOGS_DIR}/copilot-*.log 2>/dev/null || true",
            timeout=15,
        )
    except Exception:
        ssh.disconnect()
        print("No copilot log files found.")
        return

    files = [f.strip() for f in out.strip().splitlines() if f.strip()]
    if not files:
        ssh.disconnect()
        print("No copilot log files found.")
        return

    entries = []
    for path in files:
        fname = path.split("/")[-1]
        m = _UUID_RE.search(fname)
        if not m:
            continue
        session_id = m.group(0)
        prefix = fname[len("copilot-"):m.start()]
        folder = prefix.rstrip("-") if prefix else ""
        timestamp = _parse_log_timestamp(fname)
        entries.append({"path": path, "session_id": session_id, "folder": folder, "timestamp": timestamp})

    if not entries:
        ssh.disconnect()
        print("No copilot log files found.")
        return

    # Non-interactive fallback (e.g., piped output)
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        try:
            col_id = 38
            col_folder = 22
            col_ts = 21
            print(f"{'SESSION ID':<{col_id}}{'FOLDER':<{col_folder}}{'STARTED':<{col_ts}}")
            print("-" * (col_id + col_folder + col_ts))
            for e in entries:
                print(f"{e['session_id']:<{col_id}}{e['folder']:<{col_folder}}{e['timestamp']:<{col_ts}}")
        finally:
            ssh.disconnect()
        return

    # Interactive browser
    try:
        while True:
            _display_log_list(entries)
            try:
                choice = _read_selection(len(entries))
            except KeyboardInterrupt:
                break
            if choice is None:
                break
            entry = entries[choice - 1]
            content, _ = ssh.exec(f"cat {entry['path']} 2>/dev/null || true", timeout=30)
            _display_log_content(entry, content)
    except KeyboardInterrupt:
        pass
    finally:
        ssh.disconnect()
        _clear_screen()


def view_log(session_id: str, verbose: bool = False):
    """Display the full contents of a copilot session log in a paginated format."""
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
        _page_content(out)


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
