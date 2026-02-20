# install-windows.ps1 — syncript installer for Windows (PowerShell)
# Author: Younes Rahimi
#
# Usage:
#   .\install-windows.ps1                  # install
#   .\install-windows.ps1 -Uninstall       # remove installed files
#   .\install-windows.ps1 -Force           # reinstall even if already present
#   .\install-windows.ps1 -Server myhost -BaseRemote /home/user -Port 22
#
# What this script does:
#   1. Checks that Python 3 and pip are available.
#   2. Installs the PyYAML and paramiko dependencies.
#   3. Creates a syncript.cmd wrapper in %USERPROFILE%\bin (or chosen dir).
#   4. Creates a sample config at %APPDATA%\syncript\config.yaml.
#   5. Adds the install directory to the user PATH (or gives instructions).

[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$Force,
    [string]$InstallDir = "",
    [string]$Server     = "",
    [string]$BaseRemote = "",
    [int]$Port          = 22
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────

$InstallerTag = "# added by syncript installer"

function Write-Info  { param($msg) Write-Host "[syncript] $msg" -ForegroundColor Green  }
function Write-Warn  { param($msg) Write-Host "[syncript] $msg" -ForegroundColor Yellow }
function Write-Fail  { param($msg) Write-Host "[syncript] error: $msg" -ForegroundColor Red; exit 1 }

# ── Defaults ──────────────────────────────────────────────────────────────────

if (-not $InstallDir) {
    $InstallDir = Join-Path $env:USERPROFILE "bin"
}

$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$SourcePy    = Join-Path $ScriptDir "syncript.py"
$ConfigDir   = Join-Path $env:APPDATA "syncript"
$ConfigFile  = Join-Path $ConfigDir "config.yaml"
$WrapperCmd  = Join-Path $InstallDir "syncript.cmd"
$WrapperPy   = Join-Path $InstallDir "syncript_run.py"

# ── Uninstall ─────────────────────────────────────────────────────────────────

if ($Uninstall) {
    Write-Info "Uninstalling syncript …"

    foreach ($f in @($WrapperCmd, $WrapperPy)) {
        if (Test-Path $f) {
            Remove-Item $f -Force
            Write-Info "Removed $f"
        }
    }

    # Remove InstallDir from user PATH if it was added by this installer
    $currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
    if ($currentPath -like "*$InstallDir*") {
        $newPath = ($currentPath -split ";" | Where-Object { $_ -ne $InstallDir }) -join ";"
        [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
        Write-Info "Removed $InstallDir from user PATH."
    }

    Write-Info "Uninstall complete."
    Write-Info "Config files in $ConfigDir were NOT removed."
    Write-Info "To remove config: Remove-Item -Recurse $ConfigDir"
    exit 0
}

# ── Check Python ──────────────────────────────────────────────────────────────

Write-Info "Checking Python …"

$python = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python 3") {
            $python = $candidate
            Write-Info "Found $candidate : $ver"
            break
        }
    } catch { }
}

if (-not $python) {
    Write-Fail "Python 3 not found. Download from https://www.python.org/downloads/ and ensure it is on PATH."
}

# Check pip
try {
    & $python -m pip --version | Out-Null
} catch {
    Write-Fail "pip not found. Run: $python -m ensurepip --upgrade"
}

# ── Check source ──────────────────────────────────────────────────────────────

if (-not (Test-Path $SourcePy)) {
    Write-Fail "syncript.py not found at $SourcePy. Run this script from the syncript repo root."
}

# ── Install dependencies ──────────────────────────────────────────────────────

Write-Info "Installing Python dependencies (paramiko, pyyaml) …"
& $python -m pip install --quiet --user paramiko pyyaml

# ── Create install directory ──────────────────────────────────────────────────

if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir | Out-Null
    Write-Info "Created $InstallDir"
}

# ── Create wrapper files ──────────────────────────────────────────────────────

if ((Test-Path $WrapperCmd) -and -not $Force) {
    Write-Info "syncript already installed at $WrapperCmd (use -Force to reinstall)."
} else {
    # .cmd wrapper so 'syncript' works from cmd.exe and PowerShell
    @"
@echo off
python "%~dp0syncript_run.py" %*
"@ | Set-Content -Path $WrapperCmd -Encoding ASCII
    Write-Info "Created $WrapperCmd"

    # Python runner
    @"
#!/usr/bin/env python3
"""syncript CLI wrapper — installed by install-windows.ps1"""
import sys
from syncript.cli import main
sys.exit(main() or 0)
"@ | Set-Content -Path $WrapperPy -Encoding UTF8
    Write-Info "Created $WrapperPy"
}

# ── Update user PATH ──────────────────────────────────────────────────────────

$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($currentPath -notlike "*$InstallDir*") {
    $newPath = "$InstallDir;$currentPath"
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Write-Info "Added $InstallDir to user PATH."
    Write-Warn "Restart your terminal (or log out and in) for the PATH change to take effect."
} else {
    Write-Info "$InstallDir is already on user PATH."
}

# ── Create sample global config ───────────────────────────────────────────────

if (-not (Test-Path $ConfigDir)) {
    New-Item -ItemType Directory -Path $ConfigDir | Out-Null
}

if ((Test-Path $ConfigFile) -and -not $Force) {
    Write-Info "Config already exists at $ConfigFile (use -Force to recreate)."
} else {
    # Prompt for defaults interactively if not provided via flags
    if (-not $Server -and [Environment]::UserInteractive) {
        $Server = Read-Host "Default server hostname (e.g. example.com)"
    }
    if (-not $BaseRemote -and [Environment]::UserInteractive) {
        $BaseRemote = Read-Host "Base remote path (e.g. /home/user, leave blank to skip)"
    }

    $baseRemoteVal = if ($BaseRemote) { $BaseRemote } else { "/home/user" }
    $serverVal     = if ($Server)     { $Server }     else { "example.com" }

    @"
# syncript global configuration
# Author: Younes Rahimi
#
# This file stores shared defaults used by 'syncript init'.
# Project-specific settings live in the project's .syncript file.
#
# Schema:
#   defaults.base_remote  — prepended to relative remote_root values during init
#   defaults.server       — default SSH server hostname
#   defaults.port         — default SSH port

profiles:
  - name: default
    # server: "example.com"       # override per project via .syncript
    # port: 22
    # local_root: "./"
    # remote_root: "projects/myrepo"   # relative to base_remote below

defaults:
  base_remote: "$baseRemoteVal"
  server: "$serverVal"
  port: $Port
"@ | Set-Content -Path $ConfigFile -Encoding UTF8
    Write-Info "Created sample config → $ConfigFile"
}

# ── SSH guidance ──────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "── SSH Setup ────────────────────────────────────────────────────────────" -ForegroundColor Cyan
Write-Host "  If you haven't set up SSH key authentication yet:" -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. Generate a key (skip if you already have one):"
Write-Host "       ssh-keygen -t ed25519 -C syncript"
Write-Host ""
Write-Host "  2. Copy your public key to the remote server:"
Write-Host "       type %USERPROFILE%\.ssh\id_ed25519.pub | ssh user@host 'mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys'"
Write-Host "     (PowerShell):"
Write-Host "       Get-Content `$env:USERPROFILE\.ssh\id_ed25519.pub | ssh user@host 'cat >> ~/.ssh/authorized_keys'"
Write-Host ""
Write-Host "  3. Test the connection:"
Write-Host "       ssh user@yourserver.com"
Write-Host ""
Write-Host "  Troubleshooting:" -ForegroundColor Yellow
Write-Host "    - Permission denied: ensure ~/.ssh on remote has chmod 700 and"
Write-Host "      authorized_keys has chmod 600."
Write-Host "    - Connection refused: check the server address and port."
Write-Host "    - Key not used: run ssh-add to load it into ssh-agent."
Write-Host "─────────────────────────────────────────────────────────────────────────" -ForegroundColor Cyan
Write-Host ""

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Info ""
Write-Info "Installation complete!"
Write-Info ""
Write-Info "Next steps:"
Write-Info "  1. Restart your terminal for the PATH change to take effect."
Write-Info "  2. In your project folder, run:   syncript init"
Write-Info "  3. Then sync with:                syncript sync"
Write-Info "  4. Check status with:             syncript status"
Write-Info ""
Write-Info "To uninstall: .\install-windows.ps1 -Uninstall"
