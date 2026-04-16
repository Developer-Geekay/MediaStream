"""
DLNA/UPnP service — pure-Python, cross-platform (Linux, macOS, Windows).
Broadcasts via SSDP and serves a MediaServer device description + content directory.
Compatible with VLC, Kodi, Smart TVs, and any UPnP/DLNA renderer.
"""
from __future__ import annotations

import platform
import socket
import struct
import threading
import uuid
import logging
from pathlib import Path, PurePosixPath
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, quote
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.sax.saxutils import escape as xml_escape

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

# Minimal ContentDirectory SCPD — clients fetch this before browsing
_SCPD_XML = b"""<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action>
      <name>Browse</name>
      <argumentList>
        <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
        <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
        <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
        <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
        <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
        <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
        <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_UpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType><allowedValueList><allowedValue>BrowseMetadata</allowedValue><allowedValue>BrowseDirectChildren</allowedValue></allowedValueList></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_UpdateID</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="yes"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""


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


def _build_didl(local_ip: str, http_port: int, media_root: Path) -> tuple[str, int]:
    """Return (didl_xml_string, item_count)."""
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
        encoded_path = quote(item["rel_url"], safe="/")
        url = f"http://{local_ip}:{http_port}/dlna/media/{encoded_path}"
        # DLNA.ORG_OP=01 = byte-seek supported (enables range requests / scrubbing)
        proto = f"http-get:*:{mime}:DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000"
        res = SubElement(el, "res", protocolInfo=proto, size=str(item["size"]))
        res.text = url

    return tostring(didl, encoding="unicode"), len(items)


def _soap_browse_response(didl_str: str, count: int) -> bytes:
    # The <Result> element must contain the DIDL-Lite as XML-escaped text,
    # NOT as raw embedded XML.  Embedding it raw produces malformed SOAP that
    # every DLNA renderer rejects with "invalid path / could not be accessed".
    escaped = xml_escape(didl_str)
    soap = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        '<u:BrowseResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
        f"<Result>{escaped}</Result>"
        f"<NumberReturned>{count}</NumberReturned>"
        f"<TotalMatches>{count}</TotalMatches>"
        "<UpdateID>1</UpdateID>"
        "</u:BrowseResponse>"
        "</s:Body>"
        "</s:Envelope>"
    )
    return soap.encode("utf-8")


class DLNAHTTPHandler(BaseHTTPRequestHandler):
    media_root: Path = None
    local_ip: str = "127.0.0.1"
    http_port: int = 8200

    def log_message(self, fmt, *args):
        logger.debug("DLNA %s %s", self.command if hasattr(self, "command") else "", fmt % args)

    def do_GET(self):
        path = unquote(self.path).split("?")[0]

        if path == "/dlna/description.xml":
            body = _device_description_xml(self.local_ip, self.http_port).encode("utf-8")
            self._respond(200, "text/xml; charset=utf-8", body)

        elif path == "/dlna/content-directory.xml":
            self._respond(200, "text/xml; charset=utf-8", _SCPD_XML)

        elif path.startswith("/dlna/media/"):
            self._serve_file(path[len("/dlna/media/"):])

        else:
            self._respond(404, "text/plain", b"Not found")

    def do_POST(self):
        path = unquote(self.path).split("?")[0]
        if path == "/dlna/control":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            if "Browse" in body:
                didl_str, count = _build_didl(self.local_ip, self.http_port, self.media_root)
                logger.info("DLNA Browse: returning %d items", count)
                self._respond(200, "text/xml; charset=utf-8", _soap_browse_response(didl_str, count))
            else:
                self._respond(200, "text/xml; charset=utf-8", b"<?xml version=\"1.0\"?><s:Envelope xmlns:s=\"http://schemas.xmlsoap.org/soap/envelope/\"><s:Body/></s:Envelope>")
        else:
            self._respond(404, "text/plain", b"Not found")

    def do_SUBSCRIBE(self):
        # Clients SUBSCRIBE to the event URL before browsing.
        # We don't implement eventing but must return 200 + SID so they proceed.
        sid = f"uuid:{uuid.uuid4()}"
        self.send_response(200)
        self.send_header("SID", sid)
        self.send_header("TIMEOUT", "Second-1800")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_UNSUBSCRIBE(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _serve_file(self, rel: str):
        file_path = (self.media_root / Path(rel)).resolve()
        if not str(file_path).startswith(str(self.media_root)):
            self._respond(403, "text/plain", b"Forbidden")
            return
        if not file_path.exists() or not file_path.is_file():
            logger.warning("DLNA: file not found: %s", file_path)
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
            self.send_header("transferMode.dlna.org", "Streaming")
            self.end_headers()
            with open(file_path, "rb") as f:
                f.seek(start)
                self.wfile.write(f.read(length))
        else:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("transferMode.dlna.org", "Streaming")
            self.end_headers()
            with open(file_path, "rb") as f:
                self.wfile.write(f.read())

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
    """Listens for UPnP M-SEARCH and sends ssdp:alive on startup."""

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
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except OSError:
                    pass
            self._sock.bind(("", self.ssdp_port))
        except OSError as e:
            logger.warning("Cannot bind SSDP port %d: %s", self.ssdp_port, e)
            return

        try:
            mreq = struct.pack("4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton(self.local_ip))
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except OSError:
            mreq = struct.pack("4sL", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        self._sock.settimeout(1.0)
        logger.info("SSDP discovery listening on %s:%d", SSDP_ADDR, self.ssdp_port)

        # Announce ourselves immediately so clients discover us without M-SEARCH
        self._send_alive()

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

    def _location(self) -> str:
        return f"http://{self.local_ip}:{self.http_port}/dlna/description.xml"

    def _send_alive(self):
        from email.utils import formatdate
        notify = (
            "NOTIFY * HTTP/1.1\r\n"
            f"HOST: {SSDP_ADDR}:{self.ssdp_port}\r\n"
            "CACHE-CONTROL: max-age=1800\r\n"
            f"LOCATION: {self._location()}\r\n"
            "NT: urn:schemas-upnp-org:device:MediaServer:1\r\n"
            "NTS: ssdp:alive\r\n"
            "SERVER: MediaStream/1.0 UPnP/1.0\r\n"
            f"USN: uuid:{DEVICE_UUID}::urn:schemas-upnp-org:device:MediaServer:1\r\n"
            "\r\n"
        )
        try:
            self._sock.sendto(notify.encode(), (SSDP_ADDR, self.ssdp_port))
        except Exception as e:
            logger.debug("SSDP alive send error: %s", e)

    def _send_response(self, addr):
        from email.utils import formatdate
        response = (
            "HTTP/1.1 200 OK\r\n"
            "CACHE-CONTROL: max-age=1800\r\n"
            f"DATE: {formatdate(usegmt=True)}\r\n"
            f"LOCATION: {self._location()}\r\n"
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
            "DLNA HTTP on %s:%d  description: http://%s:%d/dlna/description.xml",
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
            f"VLC/Kodi: open network → http://{local_ip}:{settings.dlna_http_port}/dlna/description.xml  |  "
            f"Smart TV: auto-discovered as '{settings.dlna_friendly_name}'"
        ),
    }
