"""
File scanning operations (local and remote)
"""
import time
import tempfile
import uuid
import gzip
from pathlib import Path
from ..core.ssh_manager import SSHManager
from .. import config as _cfg
from ..utils.logging import log, vlog
from ..utils.ignore_patterns import is_ignored, _stignore_to_find_prunes


def start_remote_scan(mgr: SSHManager, patterns: list) -> str:
    """
    Fire a tmux/background `find` on the remote.
    Returns the path of the remote marker file (unique per run).
    The actual TSV is compressed to a .tsv.gz; a small `.done` marker
    is written when the gzip is complete so the client can poll the marker
    and then download & decompress the gz on the local side.
    """
    scan_id = uuid.uuid4().hex
    marker_file = f"{_cfg.REMOTE_TMP}/sync_scan_{scan_id}.done"
    remote_gz = f"{_cfg.REMOTE_TMP}/sync_scan_{scan_id}.tsv.gz"
    prune_expr = _stignore_to_find_prunes(_cfg.LOCAL_ROOT)

    # find outputs: rel_path \t mtime_epoch \t size
    remote_root_str = str(_cfg.REMOTE_ROOT)

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
    # log(f"  find command: {find_cmd}")
    mgr.exec_nowait(find_cmd)
    return marker_file


def poll_remote_scan(mgr: SSHManager, marker_file: str,
                     poll_interval: int, poll_timeout: int) -> dict:
    """
    Poll the remote marker file until SCAN_DONE is present, then download
    the corresponding .tsv.gz, decompress it locally and parse the TSV.
    Returns the parsed scan dict.
    """
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


def local_list_all(root: Path, patterns: list) -> dict[str, tuple[float, int]]:
    """Returns {rel_posix: (mtime, size)}"""
    result: dict[str, tuple[float, int]] = {}
    skip_names = {_cfg.get_state_file().name, _cfg.get_progress_file().name, Path(_cfg.STIGNORE_FILE).name}
    for p in root.rglob("*"):
        if p.is_dir():
            continue
        if p.name in skip_names and p.parent == root:
            continue
        # Skip conflict artefacts
        if ".conflict" in p.name:
            continue
        rel = p.relative_to(root).as_posix()
        # Always exclude .git directory contents (mirrors remote scan behaviour)
        if rel == ".git" or rel.startswith(".git/"):
            continue
        if is_ignored(rel, patterns):
            continue
        st = p.stat()
        result[rel] = (st.st_mtime, st.st_size)
    return result
