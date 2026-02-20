#!/usr/bin/env python3
"""
syncript  —  Unstable-connection-tolerant bidirectional SSH sync
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
import argparse
from syncript.core.sync_engine import run_sync


def main():
    """CLI entry point for syncript"""
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
