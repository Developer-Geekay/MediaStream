#!/usr/bin/env python3
"""
MediaStream — bundled smbd downloader
======================================
Downloads and bundles a platform-appropriate smbd binary into
  resources/smbd/<platform>-<arch>/smbd
  resources/smbd/<platform>-<arch>/lib/   (required shared libraries)

After this script succeeds, MediaStream can run the SMB server without
any system-level Samba installation.

Usage:
    python scripts/download_smbd.py
    python scripts/download_smbd.py --force   # re-bundle even if already present

Platforms:
    macOS  (arm64 / x86_64) — installs via Homebrew, then bundles binary + dylibs
    Linux  (x86_64 / arm64) — downloads .deb package, extracts binary + solibs
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_ROOT  = Path(__file__).parent.parent
RESOURCES_DIR = PROJECT_ROOT / "resources" / "smbd"

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):    print(f"[smbd-bundle]  {msg}")
def ok(msg: str):     print(f"[smbd-bundle]  ✓  {msg}")
def warn(msg: str):   print(f"[smbd-bundle]  ⚠  {msg}", file=sys.stderr)
def err(msg: str):    print(f"[smbd-bundle]  ✗  {msg}", file=sys.stderr)


def get_platform_arch() -> tuple[str, str]:
    """Return ('macos'|'linux', 'arm64'|'x86_64')."""
    if sys.platform == "darwin":
        plat = "macos"
    elif sys.platform.startswith("linux"):
        plat = "linux"
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")

    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    return plat, arch


def dest_dir(plat: str, arch: str) -> Path:
    return RESOURCES_DIR / f"{plat}-{arch}"


# ══════════════════════════════════════════════════════════════════════════════
# macOS — Homebrew install + dylib bundling
# ══════════════════════════════════════════════════════════════════════════════

def _homebrew_smbd_path() -> str | None:
    """Return the Homebrew smbd path for the current machine."""
    candidates = [
        "/opt/homebrew/sbin/smbd",   # Apple Silicon
        "/usr/local/sbin/smbd",       # Intel Mac
    ]
    return next((p for p in candidates if Path(p).is_file() and os.access(p, os.X_OK)), None)


def _homebrew_prefix() -> str:
    """Return the Homebrew prefix directory."""
    result = subprocess.run(["brew", "--prefix"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "/opt/homebrew"


def _otool_libs(binary: Path) -> list[str]:
    """Use otool -L to list dynamic library dependencies of binary."""
    result = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True)
    libs = []
    for line in result.stdout.splitlines()[1:]:   # first line is the binary itself
        path = line.strip().split()[0] if line.strip() else ""
        if path:
            libs.append(path)
    return libs


def _is_homebrew_lib(path: str, brew_prefix: str) -> bool:
    """Return True if this dylib lives under the Homebrew prefix."""
    return path.startswith(brew_prefix) or path.startswith("/usr/local/opt")


def _copy_dylibs_recursive(binary: Path, lib_dir: Path, brew_prefix: str, _visited: set | None = None, depth: int = 5):
    """
    Recursively collect all Homebrew dylibs needed by binary and copy them
    into lib_dir.  Stops at depth=0 or when a lib has already been copied.
    """
    if _visited is None:
        _visited = set()
    if depth == 0:
        return

    for lib_path in _otool_libs(binary):
        if not _is_homebrew_lib(lib_path, brew_prefix):
            continue
        src = Path(lib_path)
        if not src.exists():
            warn(f"  Missing lib (skipped): {lib_path}")
            continue
        key = src.name
        if key in _visited:
            continue
        _visited.add(key)
        dest = lib_dir / key
        shutil.copy2(src, dest)
        log(f"  bundled lib: {key}")
        # Recurse into transitive dependencies
        _copy_dylibs_recursive(dest, lib_dir, brew_prefix, _visited, depth - 1)


def _rewrite_dylib_paths(binary: Path, lib_dir: Path):
    """
    Rewrite embedded library paths inside binary to use @executable_path/lib/<name>
    so the bundle works without DYLD_LIBRARY_PATH (and survives SIP stripping it).
    """
    brew_prefix = _homebrew_prefix()
    for lib_path in _otool_libs(binary):
        if not _is_homebrew_lib(lib_path, brew_prefix):
            continue
        lib_name = Path(lib_path).name
        new_path  = f"@executable_path/lib/{lib_name}"
        subprocess.run(
            ["install_name_tool", "-change", lib_path, new_path, str(binary)],
            capture_output=True,
        )
    # Also rewrite inside each bundled lib so they find each other
    for lib_file in lib_dir.glob("*.dylib"):
        for lib_path in _otool_libs(lib_file):
            if not _is_homebrew_lib(lib_path, brew_prefix):
                continue
            lib_name = Path(lib_path).name
            new_path  = f"@loader_path/{lib_name}"
            subprocess.run(
                ["install_name_tool", "-change", lib_path, new_path, str(lib_file)],
                capture_output=True,
            )


def _resign_binary(binary: Path):
    """
    Remove existing signature and ad-hoc re-sign so SIP does not strip
    DYLD_LIBRARY_PATH from the process environment.
    """
    subprocess.run(["codesign", "--remove-signature", str(binary)], capture_output=True)
    result = subprocess.run(
        ["codesign", "--force", "--sign", "-", str(binary)],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        log("  ad-hoc re-signed binary")
    else:
        warn(f"  codesign failed (non-fatal): {result.stderr.strip()}")


def download_macos(arch: str, force: bool) -> bool:
    target_dir = dest_dir("macos", arch)
    smbd_dest  = target_dir / "smbd"
    lib_dir    = target_dir / "lib"

    if smbd_dest.exists() and not force:
        ok(f"Already bundled: {smbd_dest}")
        return True

    log(f"macOS/{arch}: checking for Homebrew smbd...")

    smbd_src = _homebrew_smbd_path()
    if not smbd_src:
        # Try to install via Homebrew
        brew = shutil.which("brew")
        if not brew:
            err("Homebrew not found. Install from https://brew.sh then re-run.")
            return False
        log("Running: brew install samba  (this may take a few minutes)...")
        result = subprocess.run([brew, "install", "samba"], text=True)
        if result.returncode != 0:
            err("brew install samba failed — check output above.")
            return False
        smbd_src = _homebrew_smbd_path()
        if not smbd_src:
            err("smbd still not found after brew install. Check Homebrew output.")
            return False

    ok(f"Found smbd at: {smbd_src}")

    target_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(exist_ok=True)

    # Copy binary
    shutil.copy2(smbd_src, smbd_dest)
    smbd_dest.chmod(0o755)
    log(f"Copied smbd → {smbd_dest}")

    # Bundle dylibs
    log("Collecting Homebrew dylib dependencies (recursive)...")
    brew_prefix = _homebrew_prefix()
    _copy_dylibs_recursive(smbd_dest, lib_dir, brew_prefix)

    # Rewrite embedded paths → @executable_path/lib/
    log("Rewriting embedded library paths...")
    _rewrite_dylib_paths(smbd_dest, lib_dir)

    # Re-sign so SIP doesn't strip DYLD_LIBRARY_PATH
    _resign_binary(smbd_dest)

    ok(f"macOS bundle ready: {target_dir}")
    ok(f"  binary : {smbd_dest}")
    ok(f"  libs   : {len(list(lib_dir.glob('*.dylib')))} dylib(s) in {lib_dir}")
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Linux — download .deb, extract smbd + solibs
# ══════════════════════════════════════════════════════════════════════════════

# Ubuntu 22.04 (Jammy) package base URLs per arch
_UBUNTU_BASE = "http://archive.ubuntu.com/ubuntu/pool/main/s/samba"
_UBUNTU_BASE_ARM = "http://ports.ubuntu.com/ubuntu-ports/pool/main/s/samba"

def _apt_download(package: str, download_dir: Path) -> Path | None:
    """Use apt-get download to fetch a .deb without installing it."""
    if not shutil.which("apt-get"):
        return None
    result = subprocess.run(
        ["apt-get", "download", package],
        capture_output=True, text=True, cwd=str(download_dir),
    )
    if result.returncode != 0:
        warn(f"apt-get download {package} failed: {result.stderr.strip()}")
        return None
    debs = list(download_dir.glob(f"{package}*.deb"))
    return debs[0] if debs else None


def _extract_deb(deb_path: Path, extract_dir: Path):
    """Extract a .deb package into extract_dir using dpkg-deb."""
    subprocess.run(
        ["dpkg-deb", "--extract", str(deb_path), str(extract_dir)],
        check=True,
    )


def _find_in_dir(root: Path, name: str) -> Path | None:
    """Find a file by name anywhere under root."""
    for p in root.rglob(name):
        if p.is_file():
            return p
    return None


def _ldd_samba_libs(binary: Path) -> list[str]:
    """
    Use ldd to find shared libs needed by binary, returning only
    samba-specific ones (not system libc/libm etc.).
    """
    result = subprocess.run(["ldd", str(binary)], capture_output=True, text=True)
    libs = []
    for line in result.stdout.splitlines():
        # Format: "  libname.so.X => /path/to/lib (0x...)"
        m = re.search(r"=>\s+(/\S+)", line)
        if not m:
            continue
        path = m.group(1)
        # Only samba-specific libs (skip glibc, libm, libpthread, etc.)
        if re.search(r"(samba|wbclient|nss_winbind|winbind|ldb|talloc|tdb|tevent|heimdal|krb5|gssapi|com_err|hx509|roken|asn1|wind|hcrypto)", path, re.I):
            libs.append(path)
    return libs


def download_linux(arch: str, force: bool) -> bool:
    target_dir = dest_dir("linux", arch)
    smbd_dest  = target_dir / "smbd"
    lib_dir    = target_dir / "lib"

    if smbd_dest.exists() and not force:
        ok(f"Already bundled: {smbd_dest}")
        return True

    target_dir.mkdir(parents=True, exist_ok=True)
    lib_dir.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ms-smbd-") as tmp:
        tmp_path = Path(tmp)
        extract_dir = tmp_path / "pkg"
        extract_dir.mkdir()

        # Try apt-get download first
        log(f"Linux/{arch}: downloading samba package via apt-get...")
        deb = _apt_download("samba", tmp_path)

        if deb:
            log(f"  Downloaded: {deb.name}")
            try:
                _extract_deb(deb, extract_dir)
            except subprocess.CalledProcessError as exc:
                err(f"dpkg-deb extract failed: {exc}")
                return False
        else:
            err(
                "apt-get download not available on this system.\n"
                "Please install samba via your package manager:\n"
                "  apt install samba   OR   yum install samba   OR   dnf install samba\n"
                "Then re-run this script."
            )
            return False

        # Find smbd in extracted package
        smbd_src = _find_in_dir(extract_dir, "smbd")
        if not smbd_src:
            # Try samba package splitting: smbd may be in samba-common-bin
            log("  smbd not in samba package — trying samba-common-bin...")
            deb2 = _apt_download("samba-common-bin", tmp_path)
            if deb2:
                _extract_deb(deb2, extract_dir)
                smbd_src = _find_in_dir(extract_dir, "smbd")

        if not smbd_src:
            err("Could not find smbd binary in downloaded packages.")
            return False

        ok(f"Found smbd: {smbd_src}")

        # Copy smbd
        shutil.copy2(smbd_src, smbd_dest)
        smbd_dest.chmod(0o755)
        log(f"Copied smbd → {smbd_dest}")

        # Find and copy samba-specific shared libs via ldd
        log("Collecting samba shared library dependencies...")
        samba_libs = _ldd_samba_libs(smbd_dest)

        # Also download samba libs packages to get the .so files
        for pkg in ("libsamba-util0", "libsamba-errors0", "libtalloc2",
                    "libtdb1", "libtevent0", "libldb2", "libwbclient0"):
            _apt_download(pkg, tmp_path)

        lib_debs = list(tmp_path.glob("lib*.deb"))
        lib_extract = tmp_path / "libs"
        lib_extract.mkdir()
        for lib_deb in lib_debs:
            try:
                _extract_deb(lib_deb, lib_extract)
            except Exception:
                pass

        copied = 0
        # First copy from extracted lib packages
        for so_path in samba_libs:
            so_name = Path(so_path).name
            # Try to find in our extracted packages first
            found = _find_in_dir(lib_extract, so_name)
            src = found or (Path(so_path) if Path(so_path).exists() else None)
            if src and src.exists():
                shutil.copy2(src, lib_dir / so_name)
                log(f"  bundled lib: {so_name}")
                copied += 1
            else:
                warn(f"  lib not found (skipped): {so_name}")

        ok(f"Linux bundle ready: {target_dir}")
        ok(f"  binary : {smbd_dest}")
        ok(f"  libs   : {copied} solib(s) in {lib_dir}")
        return True


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Bundle smbd into MediaStream resources/")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if binary already exists")
    parser.add_argument("--platform", choices=["macos", "linux"],
                        help="Override platform detection (for cross-compilation)")
    parser.add_argument("--arch", choices=["arm64", "x86_64"],
                        help="Override arch detection")
    args = parser.parse_args()

    try:
        detected_plat, detected_arch = get_platform_arch()
    except RuntimeError as exc:
        err(str(exc))
        sys.exit(1)

    plat = args.platform or detected_plat
    arch = args.arch or detected_arch

    log(f"Platform: {plat}/{arch}")
    log(f"Target:   {dest_dir(plat, arch)}")
    print()

    if plat == "macos":
        success = download_macos(arch, args.force)
    elif plat == "linux":
        success = download_linux(arch, args.force)
    else:
        err(f"No bundling support for platform: {plat}")
        success = False

    if success:
        print()
        ok("Done! Restart MediaStream — it will use the bundled smbd automatically.")
        ok(f"Binary location: {dest_dir(plat, arch) / 'smbd'}")
    else:
        print()
        err("Bundling failed. See messages above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
