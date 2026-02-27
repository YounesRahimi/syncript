#!/usr/bin/env sh
# install-unix.sh — syncript installer for Unix-like systems (Linux, macOS, WSL)
# Author: Younes Rahimi
#
# Usage:
#   ./install-unix.sh              # install
#   ./install-unix.sh --uninstall  # remove installed files
#   ./install-unix.sh --force      # reinstall even if already present
#
# What this script does:
#   1. Checks that python3 and pip are available.
#   2. Installs the PyYAML and paramiko dependencies.
#   3. Copies syncript.py to ~/.local/bin/syncript (or the directory you choose).
#   4. Creates a sample config at $XDG_CONFIG_HOME/syncript/config.yaml.
#   5. Optionally appends a PATH export to your shell profile.

set -e

# ── Helpers ───────────────────────────────────────────────────────────────────

INSTALLER_TAG="# added by syncript installer"

info()    { printf '\033[1;32m[syncript]\033[0m %s\n' "$*"; }
warn()    { printf '\033[1;33m[syncript]\033[0m %s\n' "$*" >&2; }
die()     { printf '\033[1;31m[syncript]\033[0m error: %s\n' "$*" >&2; exit 1; }

# ── Defaults ──────────────────────────────────────────────────────────────────

INSTALL_DIR="${HOME}/.local/bin"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_PY="${SCRIPT_DIR}/syncript.py"

# Config paths
XDG_CFG="${XDG_CONFIG_HOME:-${HOME}/.config}"
CONFIG_DIR="${XDG_CFG}/syncript"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"

FORCE=0
UNINSTALL=0
BASE_REMOTE=""
DEFAULT_SERVER=""
DEFAULT_PORT="22"

# ── Argument parsing ──────────────────────────────────────────────────────────

for arg in "$@"; do
    case "$arg" in
        --force)        FORCE=1 ;;
        --uninstall)    UNINSTALL=1 ;;
        --base-remote=*) BASE_REMOTE="${arg#*=}" ;;
        --server=*)      DEFAULT_SERVER="${arg#*=}" ;;
        --port=*)        DEFAULT_PORT="${arg#*=}" ;;
        -h|--help)
            sed -n '2,/^set -e/p' "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            die "Unknown argument: $arg (use --help)"
            ;;
    esac
done

# ── Uninstall ─────────────────────────────────────────────────────────────────

if [ "$UNINSTALL" -eq 1 ]; then
    info "Uninstalling syncript …"

    WRAPPER="${INSTALL_DIR}/syncript"
    if [ -f "$WRAPPER" ]; then
        rm -f "$WRAPPER"
        info "Removed ${WRAPPER}"
    else
        warn "No wrapper found at ${WRAPPER}"
    fi

    # Remove PATH lines added by this installer from shell profiles
    for profile in "${HOME}/.profile" "${HOME}/.bashrc" "${HOME}/.zshrc"; do
        if [ -f "$profile" ] && grep -qF "$INSTALLER_TAG" "$profile"; then
            # Remove the two lines added by this installer (export PATH + tag)
            tmp="$(mktemp)"
            grep -v "$INSTALLER_TAG" "$profile" \
                | grep -v "export PATH.*${INSTALL_DIR}" > "$tmp" || true
            mv "$tmp" "$profile"
            info "Cleaned PATH entry from ${profile}"
        fi
    done

    info "Uninstall complete. Config files in ${CONFIG_DIR} were NOT removed."
    info "To remove config: rm -rf ${CONFIG_DIR}"
    exit 0
fi

# ── Check Python ──────────────────────────────────────────────────────────────

info "Checking Python …"
if ! command -v python3 >/dev/null 2>&1; then
    die "python3 not found. Please install Python 3.8+ and try again."
fi

PYTHON_VER=$(python3 -c 'import sys; print(sys.version_info[:2])' 2>/dev/null)
info "Found python3: ${PYTHON_VER}"

# Check pip
if ! python3 -m pip --version >/dev/null 2>&1; then
    die "pip not found. Install it with: python3 -m ensurepip --upgrade"
fi

# ── Check source file ─────────────────────────────────────────────────────────

if [ ! -f "$SOURCE_PY" ]; then
    die "syncript.py not found at ${SOURCE_PY}. Run this script from the syncript repo root."
fi

# ── Install dependencies ───────────────────────────────────────────────────────

info "Installing Python dependencies (paramiko, pyyaml) …"
if python3 -c "import paramiko, yaml" 2>/dev/null; then
    info "Python dependencies already available, skipping install."
else
    python3 -m pip install --quiet --user paramiko pyyaml 2>/dev/null \
        || python3 -m pip install --quiet --user --break-system-packages paramiko pyyaml \
        || warn "Could not install Python dependencies. Ensure paramiko and pyyaml are available."
fi

# ── Create bin directory ──────────────────────────────────────────────────────

if [ ! -d "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR"
    info "Created ${INSTALL_DIR}"
fi

# ── Copy/create the wrapper ───────────────────────────────────────────────────

WRAPPER="${INSTALL_DIR}/syncript"

if [ -f "$WRAPPER" ] && [ "$FORCE" -eq 0 ]; then
    info "syncript already installed at ${WRAPPER} (use --force to reinstall)."
else
    cat > "$WRAPPER" << 'WRAPPER_EOF'
#!/usr/bin/env python3
"""syncript CLI wrapper — installed by install-unix.sh"""
import sys
from syncript.cli import main
sys.exit(main() or 0)
WRAPPER_EOF
    chmod +x "$WRAPPER"
    info "Installed wrapper → ${WRAPPER}"
fi

# ── Update PATH in shell profile ──────────────────────────────────────────────

# Detect shell profile
PROFILE=""
if [ -n "$BASH_VERSION" ]; then
    PROFILE="${HOME}/.bashrc"
elif [ -n "$ZSH_VERSION" ]; then
    PROFILE="${HOME}/.zshrc"
else
    PROFILE="${HOME}/.profile"
fi

# Check if already on PATH
case ":${PATH}:" in
    *":${INSTALL_DIR}:"*)
        info "${INSTALL_DIR} is already on PATH."
        ;;
    *)
        if [ -f "$PROFILE" ] && grep -qF "$INSTALLER_TAG" "$PROFILE"; then
            info "PATH entry already present in ${PROFILE}."
        else
            printf '\nexport PATH="%s:$PATH" %s\n' "$INSTALL_DIR" "$INSTALLER_TAG" >> "$PROFILE"
            info "Added ${INSTALL_DIR} to PATH in ${PROFILE}"
            info "To apply now, run:  source ${PROFILE}"
        fi
        ;;
esac

# ── Create sample global config ───────────────────────────────────────────────

mkdir -p "$CONFIG_DIR"

if [ -f "$CONFIG_FILE" ] && [ "$FORCE" -eq 0 ]; then
    info "Config already exists at ${CONFIG_FILE} (use --force to recreate)."
else
    # Prompt for defaults if interactive and not provided via flags
    if [ -z "$DEFAULT_SERVER" ] && [ -t 0 ]; then
        printf 'Default server hostname (e.g. example.com): '
        read -r DEFAULT_SERVER
    fi
    if [ -z "$BASE_REMOTE" ] && [ -t 0 ]; then
        printf 'Base remote path (e.g. /home/user, leave blank to skip): '
        read -r BASE_REMOTE
    fi

    cat > "$CONFIG_FILE" << YAML_EOF
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
  base_remote: "${BASE_REMOTE:-/home/user}"
  server: "${DEFAULT_SERVER:-example.com}"
  port: ${DEFAULT_PORT}
YAML_EOF
    info "Created sample config → ${CONFIG_FILE}"
fi

# ── SSH guidance ──────────────────────────────────────────────────────────────

cat << 'SSH_GUIDE'

── SSH Setup ────────────────────────────────────────────────────────────
  If you haven't set up SSH key authentication yet:

  1. Generate a key (skip if you already have one):
       ssh-keygen -t ed25519 -C "syncript"

  2. Copy your public key to the remote server:
       ssh-copy-id user@yourserver.com
       # Or manually: cat ~/.ssh/id_ed25519.pub | ssh user@host "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"

  3. Test the connection:
       ssh user@yourserver.com

  Troubleshooting:
    - Permission denied: ensure ~/.ssh on remote has chmod 700 and authorized_keys has chmod 600.
    - Connection refused: check the server address and port (default 22).
    - Key not used: run ssh-add ~/.ssh/id_ed25519 to load it into ssh-agent.
─────────────────────────────────────────────────────────────────────────
SSH_GUIDE

# ── Done ──────────────────────────────────────────────────────────────────────

info ""
info "Installation complete!"
info ""
info "Next steps:"
info "  1. Restart your shell or run: source ${PROFILE}"
info "  2. In your project folder, run:   syncript init"
info "  3. Then sync with:                syncript sync"
info "  4. Check status with:             syncript status"
info ""
info "To uninstall: ./install-unix.sh --uninstall"
