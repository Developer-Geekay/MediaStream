FROM python:3.12-slim

LABEL maintainer="MediaStream"

# Install Samba and OpenSSL for cert generation
RUN apt-get update && apt-get install -y --no-install-recommends \
    samba \
    smbclient \
    openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Generate self-signed TLS cert for FTP if not mounted
RUN mkdir -p /app/certs && \
    openssl req -x509 -newkey rsa:4096 -keyout /app/certs/server.key \
      -out /app/certs/server.crt -days 3650 -nodes \
      -subj "/CN=mediastream.local" 2>/dev/null || true

# Create default media directories
RUN mkdir -p /app/media/movies /app/media/music /app/media/photos /app/config

EXPOSE 8080 2121 8200 1900/udp

ENV PYTHONUNBUFFERED=1
ENV CONFIG_PATH=/app/config/config.yaml

CMD ["python", "run.py"]
