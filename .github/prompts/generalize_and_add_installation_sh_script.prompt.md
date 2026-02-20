---
description: "Clear, structured LLM prompt to implement installer scripts, CLI behavior, config file, docs, and tests for syncript."
---

Goal
- Implement a reliable, cross-platform installer and a CLI wrapper named "syncript" that installs syncrypt.py, creates a sample YAML configuration supporting multiple sync profiles, and exposes three subcommands: init, sync, status.
- Deliver idempotent installers (Unix shell + Windows PowerShell/batch), a sample config, README updates, and tests where applicable.

Context & constraints
- Target: Unix-like systems with Python and Windows (PowerShell/batch).
- Use existing project layout and dependencies; avoid introducing new frameworks unless necessary.
- Scripts must be idempotent: re-running must not duplicate files or break existing configurations.
- Provide clear, actionable CLI behavior and YAML schema for multiple profiles.
- Provide SSH setup steps, PATH handling, uninstall, and troubleshooting guidance.
- Produce minimal, maintainable code and documentation; make changes limited to affected files/modules.

Functional requirements — installer behavior
- Provide two installers:
  - install-unix.sh (POSIX shell): works on Linux, macOS, WSL.
  - install-windows.ps1 or install-windows.bat (PowerShell preferred).
- Each installer must:
  - Detect Python availability and required pip features.
  - Install syncrypt.py (copy or pip install if packaged) into a chosen user-writable bin dir (~/.local/bin or %USERPROFILE%\bin) and ensure the "syncript" command is available on PATH (update shell profile or give instructions).
  - Create a sample configuration file at $XDG_CONFIG_HOME/synscript/config.yaml or %APPDATA%\syncript\config.yaml and also optionally place a .syncript template in the current directory when requested.
  - Prompt (or accept flags) for base remote path, default remote server address, and default SSH port — these defaults should be stored for use during "init".
  - Include SSH setup guidance (generate key, copy to remote via ssh-copy-id or manual instructions) and handle common SSH issues.
  - Be idempotent: check for existing installation, detect existing sample config and skip creation unless --force is provided.
  - Provide an uninstall mode that removes installed files created by the installer without touching unrelated user files.

CLI: syncript command and subcommands
- syncript init [--local LOCAL] [--remote REMOTE] [--server SERVER] [--port PORT] [--base-remote BASE]
  - Creates a YAML file named .syncript in current folder containing one profile entry (local_root, remote_root, server, port).
  - If .syncript exists in current folder, print an error and exit (no overwrite unless --force).
  - If LOCAL or REMOTE omitted, request interactively; defaults are the current folder (not home) for both local and remote roots, with remote root prefixed by the base-remote value saved during installation.
  - Validate provided paths (local must exist or be creatable; remote must be a non-empty path string).
  - Allow setting profile name to support multiple profiles in the project-level config (global config can hold defaults).
- syncript sync [--profile PROFILE]
  - Finds the nearest .syncript by searching current folder upward until root; if none found, error and exit.
  - Executes synchronization using profile settings (details of sync implementation are outside scope; ensure CLI calls existing syncrypt.py with correct args).
- syncript status [--profile PROFILE]
  - Finds nearest .syncript like sync and prints pending changes, conflicts, and last sync time from sync metadata.
- Behavior shared by subcommands:
  - Support verbose and dry-run flags.
  - Return meaningful exit codes (0 success, non-zero errors).

Config file schema (YAML)
- Support multiple profiles. Example structure:
  profiles:
    - name: default
      server: "example.com"
      port: 22
      local_root: "./"
      remote_root: "projects/myrepo"    # remote root is path relative to base_remote
  defaults:
    base_remote: "/home/user"           # prepended to remote_root during init
    server: "example.com"
    port: 22
- Provide comments in the sample config explaining fields.

Installer output and docs
- Update README.md with clear installation steps for Unix and Windows, usage examples for init/sync/status, and troubleshooting tips (SSH, PATH, permission issues).
- Include examples showing:
  - How to run init non-interactively.
  - How syncript searches parent folders for .syncript.
  - How to uninstall.

Idempotency & uninstall
- Installers must detect and respect existing installs and configs; do not blindly overwrite.
- Uninstall must remove files installed by the installer and revert PATH changes if the installer added them to user shell profiles (only remove lines previously appended by the installer).

Testing & acceptance criteria
- Provide basic integration tests (or test stubs) for installer idempotency and for CLI .syncript discovery behavior (search upward).
- Acceptance criteria:
  - install-unix.sh installs syncript without duplicating files on repeated runs.
  - install-windows.ps1 installs syncript on Windows and documents manual steps when automatic PATH changes are not possible.
  - syncript init creates a valid .syncript YAML and refuses to overwrite unless forced.
  - syncript sync/status locate .syncript by searching parent folders.
  - README contains clear usage and uninstall instructions.

Deliverables
- install-unix.sh (POSIX shell), install-windows.ps1 (PowerShell) or install-windows.bat, both idempotent.
- Sample config YAML and comments.
- Updated README.md with installation and usage sections.
- Minimal integration tests for installer idempotency and .syncript discovery.
- Clear troubleshooting and uninstall instructions.

Notes for implementation (concise)
- Prefer explicit checks and user confirmations over assumptions.
- Keep changes minimal to existing code; follow project naming & style.
- If uncertain about sync internals, implement CLI glue that invokes existing syncrypt.py with validated args and document behavior.
- Use Younes Rahimi as author in generated comments.

Examples (for LLM to produce)
- show install-unix.sh that:
  - detects python3, pip, creates ~/.local/bin if needed, copies syncrypt.py, makes syncript symlink/wrapper, appends PATH export to ~/.profile if necessary, creates config at $XDG_CONFIG_HOME/syncript/config.yaml, sets base_remote defaults, and supports --uninstall and --force flags.
- show syncript init behavior that:
  - validates or interactively requests local/remote, writes .syncript YAML, refuses overwrite unless --force.
