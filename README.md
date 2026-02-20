# syncript v2 — Unstable-Connection-Tolerant Bidirectional SSH Sync

Syncs `C:\Users\bs\projects\jibit\cloud`  ↔  `root@136.0.10.24:9011:/root/projects/jibit/cloud`

> **📦 Now refactored into a modular Python package!** See [REFACTORING_NOTES.md](REFACTORING_NOTES.md) for details.

---

## Quick Start

```bash
pip install paramiko

# Original way (still works)
python syncript.py --dry-run   # preview first
python syncript.py             # real sync

# Or as a Python module
python -m syncript --dry-run
python -m syncript
```

---

## What Changed from v1 (and Why)

| Problem in v1 | Fix in v2 |
|---|---|
| Remote scan = N SFTP round-trips (one per directory) | One `find` command, result polled from a temp file |
| MD5 comparison = reading every remote file byte | mtime + size comparison only — zero remote reads |
| One drop kills everything, no recovery | Checkpoint file — resume from exact last position |
| Files sent one-by-one over SFTP | tar+gzip batching — N files = 1 TCP transfer |
| No retry on flaky connections | Retry-with-backoff decorator on every remote call |
| SSH channel dies on long transfers | Keep-alive every 30s + auto-reconnect |

---

## How It Works (in order)

```
1. Fire `nohup find … > /tmp/sync_scan_<UUID>.tsv &`  on remote
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

"Changed" = mtime differs by > 2s **or** size differs.  No MD5 needed.

---

## Checkpoint / Resume

A `.sync_progress.json` file is written to your local root during every sync.
It records which files have been pushed/pulled in the current session.

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

## Options

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

## SSH Authentication

The script uses your **ssh-agent** or `~/.ssh/id_*` keys automatically.

For a specific key, set at the top of `sync.py`:
```python
SSH_KEY_PATH = r"C:\Users\bs\.ssh\id_rsa"
```

For password auth (not recommended):
```python
SSH_PASSWORD = "your_password"
```

---

## Files Created

| File | Purpose |
|------|---------|
| `.sync_state.json` | Records last-synced mtime+size per file. Safe to delete (triggers full rescan on next run). |
| `.sync_progress.json` | Checkpoint for current session. Auto-deleted on clean finish. |
| `/tmp/sync_scan_<UUID>.tsv` | Temporary remote scan output. Auto-deleted after reading. |
| `/tmp/sync_push_<UUID>.tar.gz` | Temporary upload bundle. Auto-deleted after extraction. |
| `/tmp/sync_pull_<UUID>.tar.gz` | Temporary download bundle. Auto-deleted after extraction. |
| `*.remote.TIMESTAMP.conflict` | Remote version of a conflicting file. |
| `*.TIMESTAMP.conflict-info` | Human-readable conflict explanation. |

Add to `.gitignore`:
```
.sync_state.json
.sync_progress.json
*.conflict
*.conflict-info
```

---

## Remote Scan Internals

The remote scan command is:
```bash
nohup sh -c '
  find /root/projects/jibit/cloud \
    \( -name "*.jar" -prune \) -o \( -name "node_modules" -prune \) -o ... \
    -type f -printf "%P\t%T@\t%s\n" 2>/dev/null \
    | awk -F"\t" '{if ($1 != "") print $0}' \
    > /tmp/sync_scan_abc123.tsv \
    && echo SCAN_DONE >> /tmp/sync_scan_abc123.tsv
' >/dev/null 2>&1 &
```

- `nohup … &` — detached process, survives SSH disconnect
- `-printf "%P\t%T@\t%s\n"` — relative path, mtime epoch (float), size — **no extra stat calls**
- `SCAN_DONE` sentinel — tells the client the file is complete, not half-written
- UUID in filename — safe for concurrent runs, easy to clean up

---

## Automation (Windows Task Scheduler)

`run_sync.bat`:
```bat
@echo off
cd /d C:\Users\bs\projects\jibit\cloud
python C:\path\to\sync.py >> sync.log 2>&1
```

Schedule every 5–10 minutes in Task Scheduler.  Consecutive runs are safe —
if one is still running when the next fires, the second will just find nothing
to do (or resume if the first crashed).
