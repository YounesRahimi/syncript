"""
Main sync engine - decision logic and orchestration
"""
import sys
import traceback
from pathlib import Path
from typing import Optional
from ..config import LOCAL_ROOT, REMOTE_ROOT, REMOTE_TMP, SSH_HOST, SSH_PORT, SSH_USER, STIGNORE_FILE, MTIME_TOLERANCE
from ..core.ssh_manager import SSHManager
from ..utils.logging import log, vlog, warn, set_verbose
from ..utils.ignore_patterns import load_ignore_patterns, is_ignored
from ..utils.file_utils import _file_changed
from ..operations.scanner import start_remote_scan, poll_remote_scan, local_list_all
from ..operations.transfer import push_batch, pull_batch
from ..operations.delete import delete_remote, _confirm_deletions_by_leaf
from ..operations.conflict import check_existing_conflicts, save_conflict
from ..state.state_manager import load_state, save_state
from ..state.progress_manager import load_progress, save_progress, clear_progress

BATCH_SIZE = 100  # max files per tar batch


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


def run_sync(dry_run=False, verbose=False, force=False,
             push_only=False, pull_only=False,
             poll_interval=5, poll_timeout=120):
    set_verbose(verbose)

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
