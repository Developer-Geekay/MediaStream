#!/usr/bin/env bash
# =============================================================================
# MediaStream — Samba auto-installer for macOS
# Installs Samba via Homebrew.
# Does NOT require sudo (Homebrew installs to user-owned prefix).
# =============================================================================
set -euo pipefail

log() { echo "[MediaStream] $*"; }
err() { echo "[MediaStream] ERROR: $*" >&2; exit 1; }

# ── Homebrew check ────────────────────────────────────────────────────────────
if ! command -v brew &>/dev/null; then
    err "Homebrew is not installed. Install it first: https://brew.sh
    Then re-run this script or click 'Install' again in the web UI."
fi

BREW_VERSION=$(brew --version | head -1)
log "Homebrew found: $BREW_VERSION"

# ── Install samba ─────────────────────────────────────────────────────────────
log "Installing samba via Homebrew..."
brew install samba

# ── Verify ────────────────────────────────────────────────────────────────────
if ! command -v smbd &>/dev/null; then
    # Homebrew may install smbd under the Homebrew prefix, not in /usr/local/bin
    BREW_PREFIX=$(brew --prefix)
    SMBD_CANDIDATE="$BREW_PREFIX/sbin/smbd"
    if [ -x "$SMBD_CANDIDATE" ]; then
        log "smbd found at $SMBD_CANDIDATE"
        log "Add Homebrew sbin to PATH:"
        log "  echo 'export PATH=\"$BREW_PREFIX/sbin:\$PATH\"' >> ~/.zprofile"
        log "  source ~/.zprofile"
        log "Then restart MediaStream."
    else
        err "smbd not found after install. Check brew output above."
    fi
else
    SMBD_VERSION=$(smbd --version 2>&1 | head -1)
    log "smbd ready: $SMBD_VERSION"
fi

log "Installation complete. Restart MediaStream to start the SMB server."
