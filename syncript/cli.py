#!/usr/bin/env python3
"""
syncript  —  Unstable-connection-tolerant bidirectional SSH sync
================================================================
Author: Younes Rahimi

Subcommands:
  init      Create a .syncript config file in the current directory.
  sync      Run a bidirectional sync using the nearest .syncript config.
  status    Show pending changes and last sync time from sync metadata.
  copilot   Run the copilot CLI on the remote server and stream its output.

Run 'syncript <subcommand> --help' for more details.
"""
import sys
import argparse
from pathlib import Path


# ── init ─────────────────────────────────────────────────────────────────────

def cmd_init(args):
    """Create a .syncript profile file in the current directory."""
    from syncript import config as _cfg

    target = Path.cwd() / ".syncript"

    if target.exists() and not args.force:
        print(f"error: .syncript already exists in {Path.cwd()}", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        sys.exit(1)

    # Load global defaults
    global_cfg = _cfg.load_global_config()
    g_defaults = global_cfg.get("defaults", {})

    # Resolve local root
    local_root = args.local or str(Path.cwd())
    local_path = Path(local_root).expanduser()
    if not local_path.exists():
        if args.verbose:
            print(f"  Local path does not exist yet: {local_path}")
    local_root = str(local_path)

    # Resolve remote root
    remote_root = args.remote
    if not remote_root:
        # Build a smarter default: base_remote + cwd-relative-path-without-home-prefix
        base_remote = args.base_remote or g_defaults.get("base_remote", "")
        cwd = Path.cwd().expanduser().resolve()
        home = Path.home().expanduser().resolve()
        # Compute path of cwd with the home prefix removed when possible
        try:
            if cwd.parts[: len(home.parts)] == home.parts:
                rel_parts = cwd.parts[len(home.parts):]
            else:
                # Not under home: drop root/drive component (first part) to mimic "path without leading root"
                rel_parts = cwd.parts[1:] if len(cwd.parts) > 1 else cwd.parts
        except Exception:
            rel_parts = cwd.parts[1:] if len(cwd.parts) > 1 else cwd.parts

        rel_path_posix = Path(*rel_parts).as_posix() if rel_parts else cwd.name
        if not rel_path_posix:
            rel_path_posix = cwd.name

        if base_remote:
            # Combine base_remote and the relative path (ensure single slash)
            default_rr = f"{str(base_remote).rstrip('/')}/{rel_path_posix}"
        else:
            default_rr = rel_path_posix

        if sys.stdin.isatty():
            prompt_hint = f" [{default_rr}]" if default_rr else ""
            entered = input(f"Remote path (relative to base_remote){prompt_hint}: ").strip()
            remote_root = entered if entered else default_rr
        else:
            remote_root = default_rr

    if not remote_root:
        print("error: remote path is required.", file=sys.stderr)
        sys.exit(1)

    # Resolve server
    server = args.server or g_defaults.get("server", "example.com")
    if not args.server and sys.stdin.isatty():
        val = input(f"Server hostname [{server}]: ").strip()
        if val:
            server = val

    # Resolve user
    user = args.user or g_defaults.get("user", "root")
    if not args.user and sys.stdin.isatty():
        val = input(f"SSH user [{user}]: ").strip()
        if val:
            user = val

    # Resolve port
    port = args.port or int(g_defaults.get("port", 22))
    if not args.port and sys.stdin.isatty():
        val = input(f"SSH port [{port}]: ").strip()
        if val:
            try:
                port = int(val)
            except ValueError:
                print("error: port must be a number.", file=sys.stderr)
                sys.exit(1)

    # Resolve base_remote
    base_remote = args.base_remote or g_defaults.get("base_remote", "")

    profile_name = args.profile or "default"

    def _yq(value: str) -> str:
        """Wrap a string in YAML single quotes, escaping embedded single quotes."""
        return "'" + value.replace("'", "''") + "'"

    # Always use forward slashes in paths to avoid YAML backslash escape issues
    local_root_yaml = local_root.replace("\\", "/")

    lines = [
        "# .syncript — syncript project configuration",
        "# Author: Younes Rahimi",
        "#",
        "# profiles: list of sync profiles for this project.",
        "# Each profile has: name, server, port, local_root, remote_root.",
        "# remote_root is relative to defaults.base_remote when it does not start with '/'.",
        "profiles:",
        f"  - name: {profile_name}",
        f"    server: {_yq(server)}",
        f"    port: {port}",
        f"    user: {_yq(user)}",
        f"    local_root: {_yq(local_root_yaml)}",
        f"    remote_root: {_yq(remote_root)}",
        f"    batch_file_size: {_cfg.BATCH_FILE_SIZE}",
    ]

    if base_remote:
        lines += [
            "defaults:",
            f"  base_remote: {_yq(base_remote)}",
            f"  server: {_yq(server)}",
            f"  port: {port}",
        ]

    content = "\n".join(lines) + "\n"

    if args.dry_run:
        print(f"[dry-run] Would write {target}:")
        print(content)
        # Show .stignore that would be created
        stignore_path = Path.cwd() / ".stignore"
        stignore_content = """# File extensions
**/*.jar
**/*.xlsx
**/*.zip
**/*.iml
**/*.swp
**/*.log
*.xlsx
*.zip
*.csv
*.iml
*.swp
*.conflict
*.conflict-info
*.log

# Directories (prune entire trees)
**/node_modules/**
**/target/**
temp/**
.idea/**
.stfolder/**

# Sync metadata
.sync_state.sync-conflict-*.json
.sync_state.json
.sync_progress.json
.sync_skipped_deletions.json


.vscode/**

.DS_Store
__pycache__/**
tests/__pycache__/**
**/*.pyc
.pytest_cache/**
"""
        if not stignore_path.exists():
            print(f"[dry-run] Would write {stignore_path}:")
            print(stignore_content)
        else:
            if args.verbose:
                print(f"{stignore_path} already exists; would not overwrite.")
        return

    target.write_text(content, encoding="utf-8")
    print(f"Created {target}")

    # Create a .stignore file in the project root if it does not already exist
    stignore_path = Path.cwd() / ".stignore"
    stignore_content = """# File extensions
**/*.jar
**/*.xlsx
**/*.zip
**/*.iml
**/*.swp
**/*.log
*.xlsx
*.zip
*.csv
*.iml
*.swp
*.conflict
*.conflict-info
*.log

# Directories (prune entire trees)
**/node_modules/**
**/target/**
temp/**
.idea/**
.stfolder/**

# Sync metadata
.sync_state.sync-conflict-*.json
.sync_state.json
.sync_progress.json
.sync_skipped_deletions.json


.vscode/**

.DS_Store
__pycache__/**
tests/__pycache__/**
**/*.pyc
.pytest_cache/**
"""
    if not stignore_path.exists():
        stignore_path.write_text(stignore_content, encoding="utf-8")
        print(f"Created {stignore_path}")
        if args.verbose:
            print(stignore_content)
    else:
        if args.verbose:
            print(f"{stignore_path} already exists; not modified.")

    if args.verbose:
        print(content)


# ── sync ─────────────────────────────────────────────────────────────────────

def cmd_sync(args):
    """Run sync using the nearest .syncript config file."""
    import syncript.config as _cfg
    from syncript.core.sync_engine import run_sync

    syncript_path = _cfg.find_syncript()
    if syncript_path is None:
        print("error: no .syncript file found in this directory or any parent.", file=sys.stderr)
        print("Run 'syncript init' to create one.", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"[config] Using {syncript_path}")

    data = _cfg.load_syncript_file(syncript_path)
    profile = _cfg.get_profile(data, args.profile or "default")
    _cfg.apply_profile(profile)

    run_sync(
        dry_run=args.dry_run,
        verbose=args.verbose,
        force=args.force,
        push_only=args.push_only,
        pull_only=args.pull_only,
        poll_interval=args.poll_interval,
        poll_timeout=args.poll_timeout,
    )


# ── status ────────────────────────────────────────────────────────────────────

def cmd_status(args):
    """Show pending changes and last sync metadata."""
    import syncript.config as _cfg
    from syncript.state.state_manager import load_state

    syncript_path = _cfg.find_syncript()
    if syncript_path is None:
        print("error: no .syncript file found in this directory or any parent.", file=sys.stderr)
        print("Run 'syncript init' to create one.", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"[config] Using {syncript_path}")

    data = _cfg.load_syncript_file(syncript_path)
    profile = _cfg.get_profile(data, args.profile or "default")
    _cfg.apply_profile(profile)

    state = load_state()
    progress_file = _cfg.get_progress_file()

    print(f"\nProfile : {profile.get('name', 'default')}")
    print(f"Local   : {_cfg.LOCAL_ROOT}")
    print(f"Remote  : {_cfg.SSH_USER}@{_cfg.SSH_HOST}:{_cfg.SSH_PORT}:{_cfg.REMOTE_ROOT}")
    print(f"Tracked : {len(state)} file(s)")

    if progress_file.exists():
        import json
        try:
            prog = json.loads(progress_file.read_text("utf-8"))
            pushed = len(prog.get("pushed", []))
            pulled = len(prog.get("pulled", []))
            if pushed or pulled:
                print(f"\n⚠  Incomplete sync session detected:")
                print(f"   Pushed so far : {pushed}")
                print(f"   Pulled so far : {pulled}")
                print("   Run 'syncript sync' to resume.")
        except Exception:
            pass
    else:
        print("\nNo in-progress sync session.")


# ── copilot ───────────────────────────────────────────────────────────────────

def cmd_copilot(args):
    """Dispatch copilot sub-subcommands."""
    from syncript.copilot_cmd import run_copilot, list_logs, view_log, stop_copilot, resume_copilot

    sub = getattr(args, "copilot_sub", None)

    if sub == "run":
        resume = getattr(args, "resume", None)
        if resume:
            resume_copilot(resume, verbose=args.verbose)
        else:
            run_copilot(
                extra_args=args.copilot_args,
                model=args.model,
                autopilot=args.autopilot,
                verbose=args.verbose,
            )
    elif sub == "logs":
        session_id = getattr(args, "session_id", None)
        if session_id:
            view_log(session_id, verbose=args.verbose)
        else:
            list_logs(verbose=args.verbose)
    elif sub == "stop":
        stop_copilot(args.session_id, verbose=args.verbose)
    else:
        # No subcommand — print help
        print("error: a subcommand is required (run, logs, stop).", file=sys.stderr)
        print("Run 'syncript copilot --help' for usage.", file=sys.stderr)
        sys.exit(1)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    """CLI entry point for syncript"""
    parser = argparse.ArgumentParser(
        prog="syncript",
        description="Unstable-connection-tolerant bidirectional SSH sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    # ── init ──────────────────────────────────────────────────────────────────
    init_p = subparsers.add_parser(
        "init",
        help="Create a .syncript config file in the current directory",
        description="Create a .syncript YAML config file for this project.",
    )
    init_p.add_argument("--local", metavar="PATH",
                        help="Local root directory (default: current directory)")
    init_p.add_argument("--remote", metavar="PATH",
                        help="Remote root path (relative to base_remote or absolute)")
    init_p.add_argument("--server", metavar="HOST",
                        help="Remote server hostname or IP")
    init_p.add_argument("--user", metavar="NAME",
                        help="SSH username (default: root)")
    init_p.add_argument("--port", type=int, metavar="N",
                        help="SSH port (default: 22)")
    init_p.add_argument("--base-remote", metavar="PATH",
                        help="Base remote path prepended to relative remote roots")
    init_p.add_argument("--profile", metavar="NAME", default="default",
                        help="Profile name to create (default: default)")
    init_p.add_argument("--force", action="store_true",
                        help="Overwrite existing .syncript")
    init_p.add_argument("-n", "--dry-run", action="store_true",
                        help="Preview without writing files")
    init_p.add_argument("-v", "--verbose", action="store_true",
                        help="Show extra output")

    # ── sync ──────────────────────────────────────────────────────────────────
    sync_p = subparsers.add_parser(
        "sync",
        help="Run bidirectional sync using the nearest .syncript config",
        description="Sync local and remote using settings from .syncript.",
    )
    sync_p.add_argument("--profile", metavar="NAME", default="default",
                        help="Profile to use (default: default)")
    sync_p.add_argument("-n", "--dry-run", action="store_true",
                        help="Preview without applying changes")
    sync_p.add_argument("-v", "--verbose", action="store_true",
                        help="Show every file, not just actions")
    sync_p.add_argument("-f", "--force", action="store_true",
                        help="Ignore state+progress cache (full rescan)")
    sync_p.add_argument("--push-only", action="store_true",
                        help="Only local→remote")
    sync_p.add_argument("--pull-only", action="store_true",
                        help="Only remote→local")
    sync_p.add_argument("--poll-interval", type=int, default=5, metavar="N",
                        help="Seconds between remote-scan polls (default: 5)")
    sync_p.add_argument("--poll-timeout", type=int, default=120, metavar="N",
                        help="Max seconds to wait for remote scan (default: 120)")

    # ── status ────────────────────────────────────────────────────────────────
    status_p = subparsers.add_parser(
        "status",
        help="Show pending changes and last sync time",
        description="Show sync status for the nearest .syncript config.",
    )
    status_p.add_argument("--profile", metavar="NAME", default="default",
                          help="Profile to use (default: default)")
    status_p.add_argument("-v", "--verbose", action="store_true",
                          help="Show extra output")
    status_p.add_argument("-n", "--dry-run", action="store_true",
                          help="(no-op for status, kept for consistency)")

    # ── copilot ───────────────────────────────────────────────────────────────
    copilot_p = subparsers.add_parser(
        "copilot",
        help="Manage copilot sessions on the remote server",
        description="Manage copilot sessions on the remote server (run, stop, view logs).",
    )
    copilot_p.add_argument("--profile", metavar="NAME", default="default",
                           help="Profile to use (default: default)")
    copilot_p.add_argument("-v", "--verbose", action="store_true",
                           help="Show extra output")

    copilot_sub = copilot_p.add_subparsers(dest="copilot_sub", metavar="ACTION")

    # copilot run
    run_p = copilot_sub.add_parser(
        "run",
        help="Execute copilot on the remote server and stream its output",
        description="Execute copilot on the remote server asynchronously and stream its output.",
    )
    run_p.add_argument("-v", "--verbose", action="store_true", help="Show extra output")
    run_p.add_argument("--model", metavar="MODEL", default=None,
                       help="Model for copilot (default: claude-sonnet-4.6)")
    run_p.add_argument("--resume", metavar="SESSION_ID", default=None,
                       help="Resume streaming an existing copilot session log")
    run_p.add_argument("--autopilot", action="store_true",
                       help="Pass --autopilot to the copilot command")
    run_p.add_argument("copilot_args", nargs=argparse.REMAINDER,
                       help="Arguments forwarded to the copilot command on the server")

    # copilot logs [session-id]
    logs_p = copilot_sub.add_parser("logs", help="List or view copilot session logs")
    logs_p.add_argument("session_id", nargs="?", metavar="SESSION_ID",
                        help="Session ID to view (omit to list all)")
    logs_p.add_argument("-v", "--verbose", action="store_true", help="Show extra output")

    # copilot stop <session-id>
    stop_p = copilot_sub.add_parser("stop", help="Stop a running copilot session")
    stop_p.add_argument("session_id", metavar="SESSION_ID",
                        help="Session ID to stop")
    stop_p.add_argument("-v", "--verbose", action="store_true", help="Show extra output")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "sync":
        if args.push_only and args.pull_only:
            sync_p.error("--push-only and --pull-only are mutually exclusive")
        cmd_sync(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "copilot":
        cmd_copilot(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
