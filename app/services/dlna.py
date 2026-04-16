"""
DLNA/UPnP service — pure-Python, cross-platform (Linux, macOS, Windows).
Broadcasts via SSDP and serves a MediaServer device description + content directory.
Compatible with VLC, Kodi, Smart TVs, and any UPnP/DLNA renderer.
"""
from __future__ import annotations

import os
import platform
import socket
import struct
import threading
import uuid
import logging
from pathlib import Path, PurePosixPath
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote
from xml.etree.ElementTree import Element, SubElement, tostring

from app.config import settings

logger = logging.getLogger("mediastream.dlna")

DEVICE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "mediastream.local"))
SSDP_ADDR = "239.255.255.250"

MIME_MAP = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
}

_IS_WINDOWS = platform.system() == "Windows"


def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _device_description_xml(local_ip: str, http_port: int) -> str:
    root = Element("root", xmlns="urn:schemas-upnp-org:device-1-0")
    spec = SubElement(root, "specVersion")
    SubElement(spec, "major").text = "1"
    SubElement(spec, "minor").text = "0"

    device = SubElement(root, "device")
    SubElement(device, "deviceType").text = "urn:schemas-upnp-org:device:MediaServer:1"
    SubElement(device, "friendlyName").text = settings.dlna_friendly_name
    SubElement(device, "manufacturer").text = "MediaStream"
    SubElement(device, "manufacturerURL").text = f"http://{local_ip}:{http_port}"
    SubElement(device, "modelDescription").text = "MediaStream DLNA Server"
    SubElement(device, "modelName").text = "MediaStream"
    SubElement(device, "modelNumber").text = "1.0"
    SubElement(device, "UDN").text = f"uuid:{DEVICE_UUID}"

    service_list = SubElement(device, "serviceList")
    svc = SubElement(service_list, "service")
    SubElement(svc, "serviceType").text = "urn:schemas-upnp-org:service:ContentDirectory:1"
    SubElement(svc, "serviceId").text = "urn:upnp-org:serviceId:ContentDirectory"
    SubElement(svc, "SCPDURL").text = "/dlna/content-directory.xml"
    SubElement(svc, "controlURL").text = "/dlna/control"
    SubElement(svc, "eventSubURL").text = "/dlna/events"

    return '<?xml version="1.0"?>' + tostring(root, encoding="unicode")


def _list_media_items(media_root: Path) -> list[dict]:
    items = []
    for f in sorted(media_root.rglob("*")):
        if f.is_file() and f.suffix.lower() in MIME_MAP:
            # Always use forward slashes in URL paths regardless of OS
            rel_posix = PurePosixPath(*f.relative_to(media_root).parts)
            items.append({
                "id": str(abs(hash(str(f)))),
                "path": f,
                "rel_url": str(rel_posix),
                "name": f.name,
                "mime": MIME_MAP[f.suffix.lower()],
                "size": f.stat().st_size,
            })
    return items


def _browse_response_xml(local_ip: str, http_port: int, media_root: Path) -> str:
    items = _list_media_items(media_root)
    didl = Element(
        "DIDL-Lite",
        attrib={
            "xmlns": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
            "xmlns:dc": "http://purl.org/dc/elements/1.1/",
            "xmlns:upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
        },
    )
    for item in items:
        el = SubElement(didl, "item", id=item["id"], parentID="0", restricted="1")
        SubElement(el, "dc:title").text = item["name"]
        mime = item["mime"]
        if mime.startswith("video"):
            SubElement(el, "upnp:class").text = "object.item.videoItem"
        elif mime.startswith("audio"):
            SubElement(el, "upnp:class").text = "object.item.audioItem.musicTrack"
        else:
            SubElement(el, "upnp:class").text = "object.item.imageItem.photo"
        url = f"http://{local_ip}:{http_port}/dlna/media/{item['rel_url']}"
        res = SubElement(el, "res", protocolInfo=f"http-get:*:{mime}:*", size=str(item["size"]))
        res.text = url

    return tostring(didl, encoding="unicode")


class DLNAHTTPHandler(BaseHTTPRequestHandler):
    media_root: Path = None
    local_ip: str = "127.0.0.1"
    http_port: int = 8200

    def log_message(self, format, *args):
        logger.debug("DLNA HTTP " + format, *args)

    def do_GET(self):
        path = unquote(self.path)

        if path == "/dlna/description.xml":
            body = _device_description_xml(self.local_ip, self.http_port).encode()
            self._respond(200, "text/xml; charset=utf-8", body)

        elif path.startswith("/dlna/media/"):
            rel = path[len("/dlna/media/"):]
            # On Windows, convert forward slashes to OS separator for file lookup
            file_path = self.media_root / Path(rel)
            if not file_path.exists() or not file_path.is_file():
                self._respond(404, "text/plain", b"Not found")
                return
            mime = MIME_MAP.get(file_path.suffix.lower(), "application/octet-stream")
            file_size = file_path.stat().st_size
            range_header = self.headers.get("Range")
            if range_header:
                start, end = self._parse_range(range_header, file_size)
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(file_path, "rb") as f:
                    f.seek(start)
                    self.wfile.write(f.read(length))
            else:
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(file_size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())
        else:
            self._respond(404, "text/plain", b"Not found")

    def do_POST(self):
        if unquote(self.path) == "/dlna/control":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            if "Browse" in body:
                didl = _browse_response_xml(self.local_ip, self.http_port, self.media_root)
                count = len(_list_media_items(self.media_root))
                soap = f"""<?xml version="1.0"?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"
            s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">
  <s:Body>
    <u:BrowseResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">
      <Result>{didl}</Result>
      <NumberReturned>{count}</NumberReturned>
      <TotalMatches>{count}</TotalMatches>
      <UpdateID>1</UpdateID>
    </u:BrowseResponse>
  </s:Body>
</s:Envelope>"""
                self._respond(200, "text/xml; charset=utf-8", soap.encode())
            else:
                self._respond(200, "text/xml", b"<ok/>")
        else:
            self._respond(404, "text/plain", b"Not found")

    def _respond(self, code: int, content_type: str, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _parse_range(self, header: str, file_size: int) -> tuple[int, int]:
        _, rng = header.split("=", 1)
        start_str, end_str = rng.strip().split("-", 1)
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        return start, min(end, file_size - 1)


class SSDPServer(threading.Thread):
    """
    Listens for UPnP M-SEARCH requests and replies with our device location.
    Cross-platform: handles Windows socket quirks (no SO_REUSEPORT, different multicast join).
    """

    def __init__(self, local_ip: str, http_port: int, ssdp_port: int):
        super().__init__(daemon=True, name="ssdp-server")
        self.local_ip = local_ip
        self.http_port = http_port
        self.ssdp_port = ssdp_port
        self._stop_event = threading.Event()
        self._sock: socket.socket | None = None

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # SO_REUSEPORT is not available on Windows
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except (AttributeError, OSError):
                    pass
            self._sock.bind(("", self.ssdp_port))
        except OSError as e:
            logger.warning(
                "Cannot bind SSDP port %d: %s  "
                "(on Linux/macOS run as root; on Windows run as Administrator, "
                "or disable 'Function Discovery' service to free the port)",
                self.ssdp_port, e,
            )
            return

        # Join the UPnP multicast group
        try:
            mreq = struct.pack("4s4s",
                socket.inet_aton(SSDP_ADDR),
                socket.inet_aton(self.local_ip),
            )
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            # Fallback: join on INADDR_ANY
            mreq = struct.pack("4sL", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        self._sock.settimeout(1.0)
        logger.info("SSDP discovery listening on %s:%d", SSDP_ADDR, self.ssdp_port)

        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(1024)
                msg = data.decode("utf-8", errors="replace")
                if "M-SEARCH" in msg and (
                    "ssdp:all" in msg
                    or "MediaServer" in msg
                    or "ContentDirectory" in msg
                    or "upnp:rootdevice" in msg
                ):
                    self._send_response(addr)
            except socket.timeout:
                continue
            except Exception as e:
                logger.debug("SSDP recv error: %s", e)

    def _send_response(self, addr):
        from email.utils import formatdate
        location = f"http://{self.local_ip}:{self.http_port}/dlna/description.xml"
        response = (
            "HTTP/1.1 200 OK\r\n"
            "CACHE-CONTROL: max-age=1800\r\n"
            f"DATE: {formatdate(usegmt=True)}\r\n"
            f"LOCATION: {location}\r\n"
            "SERVER: MediaStream/1.0 UPnP/1.0\r\n"
            "ST: urn:schemas-upnp-org:device:MediaServer:1\r\n"
            f"USN: uuid:{DEVICE_UUID}::urn:schemas-upnp-org:device:MediaServer:1\r\n"
            "EXT:\r\n\r\n"
        )
        try:
            self._sock.sendto(response.encode(), addr)
        except Exception as e:
            logger.debug("SSDP send error: %s", e)

    def stop(self):
        self._stop_event.set()
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


_http_server: HTTPServer | None = None
_ssdp_server: SSDPServer | None = None
_http_thread: threading.Thread | None = None


def start_dlna_server() -> None:
    global _http_server, _ssdp_server, _http_thread
    local_ip = _get_local_ip()
    http_port = settings.dlna_http_port
    media_root = Path(settings.media_root).resolve()

    DLNAHTTPHandler.media_root = media_root
    DLNAHTTPHandler.local_ip = local_ip
    DLNAHTTPHandler.http_port = http_port

    _http_server = HTTPServer(("0.0.0.0", http_port), DLNAHTTPHandler)

    def _run_http():
        logger.info(
            "DLNA HTTP server on %s:%d  description: http://%s:%d/dlna/description.xml",
            local_ip, http_port, local_ip, http_port,
        )
        _http_server.serve_forever()

    _http_thread = threading.Thread(target=_run_http, daemon=True, name="dlna-http")
    _http_thread.start()

    _ssdp_server = SSDPServer(local_ip, http_port, settings.dlna_ssdp_port)
    _ssdp_server.start()


def stop_dlna_server() -> None:
    global _http_server, _ssdp_server
    if _http_server:
        _http_server.shutdown()
        _http_server = None
    if _ssdp_server:
        _ssdp_server.stop()
        _ssdp_server = None


def dlna_status() -> dict:
    local_ip = _get_local_ip()
    return {
        "running": _http_server is not None,
        "local_ip": local_ip,
        "http_port": settings.dlna_http_port,
        "description_url": f"http://{local_ip}:{settings.dlna_http_port}/dlna/description.xml",
        "friendly_name": settings.dlna_friendly_name,
        "connect_hint": (
            f"VLC/Kodi: add network stream → http://{local_ip}:{settings.dlna_http_port}/dlna/description.xml  |  "
            f"Smart TV: auto-discovered via UPnP as '{settings.dlna_friendly_name}'"
        ),
    }
