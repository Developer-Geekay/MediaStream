#!/usr/bin/env bash
set -e

echo "=== MediaStream Setup ==="

# Install system dependencies
if command -v apt-get &>/dev/null; then
  sudo apt-get update -qq
  sudo apt-get install -y python3-pip python3-venv openssl samba
fi

# Create virtualenv
python3 -m venv .venv
source .venv/bin/activate

# Install Python deps
pip install --upgrade pip -q
pip install -r requirements.txt -q

# Generate TLS cert if not present
if [ ! -f certs/server.crt ]; then
  mkdir -p certs
  openssl req -x509 -newkey rsa:2048 -keyout certs/server.key \
    -out certs/server.crt -days 3650 -nodes \
    -subj "/CN=mediastream.local" 2>/dev/null
  echo "TLS certificate generated."
fi

# Create media directories
mkdir -p media/movies media/music media/photos config

echo ""
echo "Setup complete!"
echo ""
echo "To start MediaStream:"
echo "  source .venv/bin/activate && python run.py"
echo ""
echo "Web UI: http://$(hostname -I | awk '{print $1}'):8080"
echo "Default login: admin / admin1234  (CHANGE THIS IMMEDIATELY)"
echo ""
