# syncript v2 — Unstable-Connection-Tolerant Bidirectional SSH Sync

> **📦 Now with cross-platform installers and a generalised CLI!** See below for installation instructions.

---

## Installation

### Unix / macOS / WSL

```bash
# Clone the repository
git clone https://github.com/ynsr/syncript.git
cd syncript

# Run the installer
chmod +x install-unix.sh
./install-unix.sh

# Reload your shell profile to update PATH
source ~/.profile   # or ~/.bashrc / ~/.zshrc
```

The installer will:
1. Verify Python 3 and pip are available.
2. Install `paramiko` and `pyyaml` dependencies.
3. Create a `syncript` wrapper in `~/.local/bin/`.
4. Generate a sample global config at `~/.config/syncript/config.yaml`.
5. Add `~/.local/bin` to your `PATH` if needed.

**Options:**

```bash
./install-unix.sh --server=myhost.com --base-remote=/home/user  # set defaults
./install-unix.sh --force                                         # reinstall / recreate config
./install-unix.sh --uninstall                                     # remove installed files
```

### Windows (PowerShell)

```powershell
# Run the installer (may need to allow script execution)
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\install-windows.ps1

# Or with options
.\install-windows.ps1 -Server myhost.com -BaseRemote /home/user -Port 22

# Uninstall
.\install-windows.ps1 -Uninstall
```

The installer creates a `syncript.cmd` wrapper in `%USERPROFILE%\bin` and adds it to your user PATH.

---

## Quick Start

```bash
pip install paramiko pyyaml   # if not using the installer

# 1. Initialise a project config in your local folder
cd /path/to/your/project
syncript init --server myhost.com --remote projects/myrepo --base-remote /home/user

# 2. Run a sync (dry-run first to preview)
syncript sync --dry-run
syncript sync

# 3. Check status
syncript status
```

---

## Subcommands

### `syncript init`

Creates a `.syncript` YAML configuration file in the current directory.

```bash
syncript init [options]

Options:
  --local PATH        Local root directory (default: current directory)
  --remote PATH       Remote path (relative to --base-remote, or absolute)
  --server HOST       SSH server hostname or IP
  --port N            SSH port (default: 22)
  --base-remote PATH  Base remote path prepended to relative remote roots
  --profile NAME      Profile name (default: "default")
  --force             Overwrite an existing .syncript
  -n, --dry-run       Preview without writing files
  -v, --verbose       Show extra output
```

**Example — non-interactive:**
```bash
syncript init \
  --server myhost.com \
  --port 22 \
  --remote projects/myrepo \
  --base-remote /home/user \
  --profile default
```

**Example — interactive** (prompts for missing values):
```bash
syncript init --server myhost.com
# Remote path (relative to base_remote): projects/myrepo
```

`.syncript` will be created with content similar to:

```yaml
# .syncript — syncript project configuration
profiles:
  - name: default
    server: "myhost.com"
    port: 22
    local_root: "/path/to/your/project"
    remote_root: "projects/myrepo"
defaults:
  base_remote: "/home/user"
  server: "myhost.com"
  port: 22
```

---

### `syncript sync`

Runs a bidirectional sync using the nearest `.syncript` config file found by searching upward from the current directory.

```bash
syncript sync [options]

Options:
  --profile NAME      Profile to use (default: "default")
  -n, --dry-run       Preview without applying changes
  -v, --verbose       Show every file evaluated, not just actions
  -f, --force         Ignore state + progress cache (full rescan)
  --push-only         Only local → remote
  --pull-only         Only remote → local
  --poll-interval N   Seconds between remote-scan polls (default: 5)
  --poll-timeout N    Max seconds to wait for remote scan (default: 120)
```

**How `.syncript` discovery works:** syncript searches the current directory, then each parent directory in turn until it finds a `.syncript` file or reaches the filesystem root. This means you can run `syncript sync` from any subdirectory of your project.

---

### `syncript status`

Shows current sync metadata without running a sync.

```bash
syncript status [--profile NAME] [-v]
```

Output example:
```
Profile : default
Local   : /path/to/your/project
Remote  : user@myhost.com:22:/home/user/projects/myrepo
Tracked : 247 file(s)

No in-progress sync session.
```

---

## Config File Schema (YAML)

Both the global config (`~/.config/syncript/config.yaml`) and the project config (`.syncript`) use the same YAML schema:

```yaml
# Multiple profiles are supported
profiles:
  - name: default
    server: "example.com"
    port: 22
    local_root: "./"
    remote_root: "projects/myrepo"   # relative to defaults.base_remote

  - name: staging
    server: "staging.example.com"
    port: 2222
    local_root: "./"
    remote_root: "/absolute/remote/path"   # absolute path — base_remote not applied

defaults:
  base_remote: "/home/user"   # prepended to relative remote_root during init
  server: "example.com"
  port: 22
```

**Field descriptions:**

| Field | Description |
|---|---|
| `profiles[].name` | Profile identifier; select with `--profile NAME` |
| `profiles[].server` | SSH server hostname or IP |
| `profiles[].port` | SSH port |
| `profiles[].local_root` | Local directory to sync |
| `profiles[].remote_root` | Remote path (absolute, or relative to `defaults.base_remote`) |
| `defaults.base_remote` | Base path on remote, prepended to relative `remote_root` values |
| `defaults.server` | Default server used during `syncript init` |
| `defaults.port` | Default port used during `syncript init` |

---

## SSH Authentication

syncript uses your **ssh-agent** or `~/.ssh/id_*` keys automatically via `paramiko`.

**Setup (one time):**
```bash
# Generate a key
ssh-keygen -t ed25519 -C "syncript"

# Copy to remote
ssh-copy-id user@yourserver.com

# Test
ssh user@yourserver.com
```

**Troubleshooting:**
- `Permission denied`: check `~/.ssh` permissions on the remote (`chmod 700 ~/.ssh`, `chmod 600 ~/.ssh/authorized_keys`).
- `Connection refused`: verify the server address and port.
- Key not picked up: run `ssh-add ~/.ssh/id_ed25519`.

### Windows: Permission errors with ~/.ssh\config and PowerShell fix

If Windows reports permission issues when SSH reads `~/.ssh\config` (errors mentioning icacls, access denied, or inheritance), fix ownership and ACLs from an elevated PowerShell prompt (Run as Administrator). Replace the path/user as needed.

PowerShell example:
```powershell
# 1. Take ownership of the file
takeown /f "${env:USERPROFILE}\.ssh\config"

# 2. Reset the permissions to remove inherited ones
icacls "${env:USERPROFILE}\.ssh\config" /reset

# 3. Give your user Full Control
icacls "${env:USERPROFILE}\.ssh\config" /grant:r "${env:USERNAME}:F"

# 4. Remove inherited ACEs so only explicit ACLs remain
icacls "${env:USERPROFILE}\.ssh\config" /inheritance:r

# 5. Verify ACLs
icacls "${env:USERPROFILE}\.ssh\config"
```

Notes:
- Run PowerShell as Administrator.
- If the problematic file is a private key (e.g. `id_rsa`), apply the same steps to that file.
- On Unix/macOS ensure private keys are 600 and the .ssh dir is 700:
  ```bash
  chmod 700 ~/.ssh
  chmod 600 ~/.ssh/id_sync       # private key
  chmod 644 ~/.ssh/id_sync.pub   # public key
  ```

### Generating a new SSH key when one already exists

If you already have SSH keys in `~/.ssh` but want a dedicated key for this project (recommended to avoid clobbering existing keys), generate a new keypair with a custom filename, register the public key with your Git/remote provider, and configure SSH to use it for the project's host.

Linux / macOS (bash):
```bash
# generate a new keypair with a custom name
ssh-keygen -t ed25519 -C "syncript" -f ~/.ssh/id_syncript

# start ssh-agent and add the key
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_syncript

# set strict permissions
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_syncript
chmod 644 ~/.ssh/id_syncript.pub
```

Windows (PowerShell):
```powershell
# generate a new keypair (PowerShell may prompt)
ssh-keygen -t ed25519 -C "syncript" -f $env:USERPROFILE\.ssh\id_syncript

# enable/start the OpenSSH authentication agent and add the key
Start-Service ssh-agent
ssh-add $env:USERPROFILE\.ssh\id_syncript

# set ACLs if needed (replace with your username)
icacls $env:USERPROFILE\.ssh\id_syncript /inheritance:r
icacls $env:USERPROFILE\.ssh\id_syncript /grant:r "${env:USERNAME}:F"
```

Configure ~/.ssh/config to tie the new key to the host (change HostName/User as appropriate):

```text
Host syncript
  HostName git.example.com
  User git
  IdentityFile ~/.ssh/id_syncript
  IdentitiesOnly yes
```

On Windows, use the expanded path if required:
```text
Host syncript
  HostName git.example.com
  User git
  IdentityFile C:\Users\bs\.ssh\id_syncript
  IdentitiesOnly yes
```

Finally:
- Upload the contents of `~/.ssh/id_syncript.pub` to your Git/remote account (GitHub/GitLab/Bitbucket).
- Test connection: `ssh -T syncript` (or `ssh -vT syncript` for verbose output).

---

## How It Works (in order)

```
1. Fire `nohup find … > /tmp/sync_scan_<UUID>.tsv.gz &` on remote
         └─ runs in background, survives SSH drop
2. Scan local files (while remote is running)
3. Poll the remote temp file every 5s until "SCAN_DONE" appears
4. Decide what to do per file (see decision table below)
5. PUSH:  pack all files → one .tar.gz → upload → remote `tar x`
6. PULL:  remote `tar c` → download → extract locally
7. DELETE: one `rm -f f1 f2 f3 …` command per direction
8. CONFLICTS: download remote copy only, leave both versions for manual merge
9. Save state, clear checkpoint
```

### Decision Table (per file)

| Local | Remote | State says | Action |
|-------|--------|------------|--------|
| ✅ exists | ❌ missing | never seen | **PUSH** |
| ✅ exists | ❌ missing | was synced | remote deleted it → **DELETE LOCAL** |
| ❌ missing | ✅ exists | never seen | **PULL** |
| ❌ missing | ✅ exists | was synced | local deleted it → **DELETE REMOTE** |
| ✅ changed | ✅ unchanged | recorded | **PUSH** |
| ✅ unchanged | ✅ changed | recorded | **PULL** |
| ✅ changed | ✅ changed | recorded | **CONFLICT** |
| ✅ unchanged | ✅ unchanged | recorded | **SKIP** |

"Changed" = mtime differs by > 3 min **or** size differs. No MD5 needed.

---

## Checkpoint / Resume

A `.sync_progress.json` file is written to your local root during every sync. It records which files have been pushed/pulled in the current session.

If the connection dies mid-sync:
- Next run detects the progress file and **skips already-transferred files**
- Only the remaining work is retried
- The progress file is deleted when a session completes cleanly

You can force a full restart with `-f / --force`.

---

## Conflict Handling

When both sides changed since the last sync:
1. Your **local file is kept untouched**
2. The remote version is downloaded as `yourfile.remote.20240217T143022Z.conflict`
3. A `yourfile.20240217T143022Z.conflict-info` explains the situation

**Merge in IntelliJ:**
1. Right-click one of the two files → `Git → Compare with…` (works for non-git files too)
2. Or use **View → Compare Files** to open both side-by-side
3. After merging, delete the `.conflict*` files and run sync again

---

## Options Reference

| Flag | Description |
|------|-------------|
| `-n` / `--dry-run` | Preview without touching any files |
| `-v` / `--verbose` | Show every file evaluated, not just actions |
| `-f` / `--force` | Ignore state + progress, full rescan |
| `--push-only` | Only local → remote |
| `--pull-only` | Only remote → local |
| `--poll-interval N` | Seconds between scan polls (default: 5) |
| `--poll-timeout N` | Max wait for remote scan (default: 120) |

---

## Uninstall

### Unix
```bash
./install-unix.sh --uninstall
```
This removes `~/.local/bin/syncript` and the PATH line from your shell profile.
Config files in `~/.config/syncript/` are **not** removed (delete manually if desired).

### Windows
```powershell
.\install-windows.ps1 -Uninstall
```

---

## Files Created

| File | Purpose |
|------|---------|
| `.syncript` | Per-project sync configuration (profiles) |
| `~/.config/syncript/config.yaml` | Global defaults used by `syncript init` |
| `.sync_state.csv` | Records last-synced mtime+size per file. Safe to delete. |
| `.sync_progress.json` | Checkpoint for current session. Auto-deleted on clean finish. |
| `/tmp/sync_scan_<UUID>.tsv.gz` | Temporary remote scan output. Auto-deleted. |
| `/tmp/sync_push_<UUID>.tar.gz` | Temporary upload bundle. Auto-deleted. |
| `/tmp/sync_pull_<UUID>.tar.gz` | Temporary download bundle. Auto-deleted. |
| `*.remote.TIMESTAMP.conflict` | Remote version of a conflicting file. |
| `*.TIMESTAMP.conflict-info` | Human-readable conflict explanation. |

Add to `.gitignore`:
```
.syncript
.sync_state.csv
.sync_progress.json
*.conflict
*.conflict-info
```

---

## Automation (Windows Task Scheduler)

`run_sync.bat`:
```bat
@echo off
cd /d C:\Users\bs\projects\myrepo
syncript sync >> sync.log 2>&1
```

Schedule every 5–10 minutes in Task Scheduler. Consecutive runs are safe —
if one is still running when the next fires, the second will just find nothing
to do (or resume if the first crashed).

---

## Performance Improvements over v1

| Problem in v1 | Fix in v2 |
|---|---|
| Remote scan = N SFTP round-trips (one per directory) | One `find` command, result polled from a temp file |
| MD5 comparison = reading every remote file byte | mtime + size comparison only — zero remote reads |
| One drop kills everything, no recovery | Checkpoint file — resume from exact last position |
| Files sent one-by-one over SFTP | tar+gzip batching — N files = 1 TCP transfer |
| No retry on flaky connections | Retry-with-backoff decorator on every remote call |
| SSH channel dies on long transfers | Keep-alive every 30s + auto-reconnect |
| Hardcoded paths — one user only | YAML config + profiles — fully generalised |
