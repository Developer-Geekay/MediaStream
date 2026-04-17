#!/usr/bin/env bash
# =============================================================================
# MediaStream — Samba auto-installer for Linux
# Detects the distro's package manager and installs the samba package.
# Must be run with sudo / as root.
# =============================================================================
set -euo pipefail

log() { echo "[MediaStream] $*"; }
err() { echo "[MediaStream] ERROR: $*" >&2; exit 1; }

log "Detecting Linux package manager..."

if command -v apt-get &>/dev/null; then
    log "Detected: apt (Debian / Ubuntu / Raspberry Pi OS)"
    apt-get update -y
    apt-get install -y samba samba-common-bin
    log "Samba installed via apt."

elif command -v dnf &>/dev/null; then
    log "Detected: dnf (Fedora / RHEL 8+ / Rocky / AlmaLinux)"
    dnf install -y samba samba-common samba-client
    log "Samba installed via dnf."

elif command -v yum &>/dev/null; then
    log "Detected: yum (CentOS / RHEL 7)"
    yum install -y samba samba-common samba-client
    log "Samba installed via yum."

elif command -v pacman &>/dev/null; then
    log "Detected: pacman (Arch / Manjaro)"
    pacman -Sy --noconfirm samba
    log "Samba installed via pacman."

elif command -v zypper &>/dev/null; then
    log "Detected: zypper (openSUSE / SLES)"
    zypper install -y samba
    log "Samba installed via zypper."

elif command -v apk &>/dev/null; then
    log "Detected: apk (Alpine Linux)"
    apk add --no-cache samba
    log "Samba installed via apk."

else
    err "No supported package manager found (tried apt, dnf, yum, pacman, zypper, apk)."
fi

# Verify
if ! command -v smbd &>/dev/null; then
    err "smbd binary not found after install — check package manager output above."
fi

SMBD_VERSION=$(smbd --version 2>&1 | head -1)
log "smbd ready: $SMBD_VERSION"
log "Installation complete. Restart MediaStream to start the SMB server."
