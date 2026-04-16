#!/usr/bin/env bash
# MediaStream setup for Linux and macOS
set -e

PYTHON=${PYTHON:-python3}

echo "=== MediaStream Setup ==="
echo "Platform: $(uname -s)"

# Check Python version
if ! $PYTHON -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
  echo "ERROR: Python 3.10+ required. Current: $($PYTHON --version 2>&1)"
  exit 1
fi

# Create virtualenv
if [ ! -d ".venv" ]; then
  $PYTHON -m venv .venv
  echo "Virtual environment created."
fi

source .venv/bin/activate

# Install Python dependencies (no system packages needed — fully cross-platform)
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "Python dependencies installed."

# Create directories
mkdir -p media/movies media/music media/photos config certs

echo ""
echo "=== Setup complete! ==="
echo ""
echo "To start MediaStream:"
echo "  source .venv/bin/activate"
echo "  python run.py"
echo ""

# Detect local IP
if command -v ip &>/dev/null; then
  LOCAL_IP=$(ip route get 1 | awk '{print $7; exit}' 2>/dev/null || echo "your-ip")
elif command -v ifconfig &>/dev/null; then
  LOCAL_IP=$(ifconfig | grep 'inet ' | grep -v 127 | head -1 | awk '{print $2}' | sed 's/addr://')
else
  LOCAL_IP="your-ip"
fi

echo "Web UI:  http://${LOCAL_IP}:8080"
echo "FTP:     ftp://${LOCAL_IP}:2121  (FTPS/TLS)"
echo "SMB:     smb://${LOCAL_IP}:4450/MEDIAFILES"
echo "DLNA:    http://${LOCAL_IP}:8200/dlna/description.xml  (auto-discovered)"
echo ""
echo "Default login: admin / admin1234  <<< CHANGE THIS IMMEDIATELY"
echo ""
echo "Note: SMB port 4450 is used to avoid conflicts with the OS built-in SMB (445)."
echo "      On macOS connect via: Finder → Go → Connect to Server → smb://${LOCAL_IP}:4450"
echo "      SSDP/DLNA discovery on port 1900 may require sudo on Linux/macOS."
