"""
DLNA/UPnP service — pure-Python, cross-platform (Linux, macOS, Windows).
Broadcasts via SSDP and serves a MediaServer device description + content directory.
Compatible with VLC, Kodi, Smart TVs, Quest VR apps (BigScreen VR), and any UPnP/DLNA renderer.

BigScreen VR / Meta Quest notes:
  - BigScreen sends BrowseMetadata on ObjectID=0 before BrowseDirectChildren — both are handled.
  - Pagination (StartingIndex / RequestedCount) is respected to avoid infinite browse loops.
  - HEAD requests are answered so BigScreen can prefetch file size before streaming.
  - Files are streamed in chunks; large videos are never fully buffered in memory.
  - ContentFeatures.dlna.org is included in media responses for strict DLNA compliance.
"""
from __future__ import annotations

import platform
import socket
import struct
import threading
import uuid
import logging
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote, quote
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.sax.saxutils import escape as xml_escape

from app.config import settings

logger = logging.getLogger("mediastream.dlna")

DEVICE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "mediastream.local"))
SSDP_ADDR = "239.255.255.250"
CHUNK_SIZE = 512 * 1024  # 512 KB — stream video in chunks, never buffer the whole file

MIME_MAP = {
    ".mp4":  "video/mp4",
    ".m4v":  "video/mp4",
    ".mkv":  "video/x-matroska",
    ".avi":  "video/x-msvideo",
    ".mov":  "video/quicktime",
    ".wmv":  "video/x-ms-wmv",
    ".ts":   "video/mp2t",
    ".webm": "video/webm",
    ".mp3":  "audio/mpeg",
    ".flac": "audio/flac",
    ".wav":  "audio/wav",
    ".aac":  "audio/aac",
    ".ogg":  "audio/ogg",
    ".opus": "audio/opus",
    ".m4a":  "audio/mp4",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".webp": "image/webp",
}

# DLNA protocolInfo flags: byte-seek (OP=01) + streaming transfer mode
_DLNA_FLAGS = "DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000"

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


def _parse_browse_args(body: str) -> dict:
    """Extract Browse action arguments from the SOAP body."""
    args = {
        "ObjectID": "0",
        "BrowseFlag": "BrowseDirectChildren",
        "StartingIndex": 0,
        "RequestedCount": 0,  # 0 = return all
    }
    try:
        root = ET.fromstring(body)
        for elem in root.iter():
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if tag == "ObjectID":
                args["ObjectID"] = elem.text or "0"
            elif tag == "BrowseFlag":
                args["BrowseFlag"] = elem.text or "BrowseDirectChildren"
            elif tag == "StartingIndex":
                args["StartingIndex"] = int(elem.text or 0)
            elif tag == "RequestedCount":
                args["RequestedCount"] = int(elem.text or 0)
    except Exception:
        pass
    return args


def _build_root_container_didl(child_count: int) -> str:
    """DIDL-Lite for BrowseMetadata on ObjectID=0 (root container)."""
    didl = Element(
        "DIDL-Lite",
        attrib={
            "xmlns": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
            "xmlns:dc": "http://purl.org/dc/elements/1.1/",
            "xmlns:upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
        },
    )
    container = SubElement(
        didl, "container",
        id="0", parentID="-1", restricted="1",
        childCount=str(child_count),
    )
    SubElement(container, "dc:title").text = settings.dlna_friendly_name
    SubElement(container, "upnp:class").text = "object.container"
    return tostring(didl, encoding="unicode")


def _build_didl(
    local_ip: str,
    http_port: int,
    media_root: Path,
    start: int = 0,
    count: int = 0,
) -> tuple[str, int, int]:
    """Return (didl_xml_string, number_returned, total_matches).

    start  — StartingIndex from the Browse request (0-based)
    count  — RequestedCount (0 = all)
    """
    all_items = _list_media_items(media_root)
    total = len(all_items)

    # Paginate
    page = all_items[start:]
    if count > 0:
        page = page[:count]

    didl = Element(
        "DIDL-Lite",
        attrib={
            "xmlns": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
            "xmlns:dc": "http://purl.org/dc/elements/1.1/",
            "xmlns:upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
        },
    )
    for item in page:
        el = SubElement(didl, "item", id=item["id"], parentID="0", restricted="1")
        SubElement(el, "dc:title").text = item["name"]
        mime = item["mime"]
        if mime.startswith("video"):
            SubElement(el, "upnp:class").text = "object.item.videoItem.movie"
        elif mime.startswith("audio"):
            SubElement(el, "upnp:class").text = "object.item.audioItem.musicTrack"
        else:
            SubElement(el, "upnp:class").text = "object.item.imageItem.photo"
        encoded_path = quote(item["rel_url"], safe="/")
        url = f"http://{local_ip}:{http_port}/dlna/media/{encoded_path}"
        proto = f"http-get:*:{mime}:{_DLNA_FLAGS}"
        res = SubElement(el, "res", protocolInfo=proto, size=str(item["size"]))
        res.text = url

    return tostring(didl, encoding="unicode"), len(page), total


def _soap_browse_response(didl_str: str, number_returned: int, total_matches: int) -> bytes:
    # <Result> must contain the DIDL-Lite as XML-escaped text, not embedded raw XML.
    escaped = xml_escape(didl_str)
    soap = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        '<u:BrowseResponse xmlns:u="urn:schemas-upnp-org:service:ContentDirectory:1">'
        f"<Result>{escaped}</Result>"
        f"<NumberReturned>{number_returned}</NumberReturned>"
        f"<TotalMatches>{total_matches}</TotalMatches>"
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

    def do_HEAD(self):
        """Answer HEAD requests so clients can get file size before streaming."""
        path = unquote(self.path).split("?")[0]
        if path.startswith("/dlna/media/"):
            self._serve_file_head(path[len("/dlna/media/"):])
        elif path == "/dlna/description.xml":
            body = _device_description_xml(self.local_ip, self.http_port).encode("utf-8")
            self._send_head(200, "text/xml; charset=utf-8", len(body))
        else:
            self._send_head(404, "text/plain", 0)

    def do_POST(self):
        path = unquote(self.path).split("?")[0]
        if path != "/dlna/control":
            self._respond(404, "text/plain", b"Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8", errors="replace")

        if "Browse" not in body:
            self._respond(200, "text/xml; charset=utf-8",
                          b'<?xml version="1.0"?>'
                          b'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                          b'<s:Body/></s:Envelope>')
            return

        args = _parse_browse_args(body)
        browse_flag = args["BrowseFlag"]
        start = args["StartingIndex"]
        count = args["RequestedCount"]

        if browse_flag == "BrowseMetadata" and args["ObjectID"] == "0":
            # BigScreen (and others) request root container metadata first
            all_items = _list_media_items(self.media_root)
            didl_str = _build_root_container_didl(len(all_items))
            resp = _soap_browse_response(didl_str, 1, 1)
            logger.info("DLNA BrowseMetadata: root container (%d items)", len(all_items))
        else:
            didl_str, returned, total = _build_didl(
                self.local_ip, self.http_port, self.media_root, start, count
            )
            resp = _soap_browse_response(didl_str, returned, total)
            logger.info(
                "DLNA BrowseDirectChildren: start=%d count=%d returned=%d total=%d",
                start, count, returned, total,
            )

        self._respond(200, "text/xml; charset=utf-8", resp)

    def do_SUBSCRIBE(self):
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

    def _serve_file_head(self, rel: str):
        file_path = (self.media_root / Path(rel)).resolve()
        if not str(file_path).startswith(str(self.media_root)):
            self._send_head(403, "text/plain", 0)
            return
        if not file_path.exists() or not file_path.is_file():
            self._send_head(404, "text/plain", 0)
            return
        mime = MIME_MAP.get(file_path.suffix.lower(), "application/octet-stream")
        self._send_head(200, mime, file_path.stat().st_size)

    def _send_head(self, code: int, content_type: str, size: int):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Accept-Ranges", "bytes")
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
        content_features = f"DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000"

        if range_header:
            start, end = self._parse_range(range_header, file_size)
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("transferMode.dlna.org", "Streaming")
            self.send_header("ContentFeatures.dlna.org", content_features)
            self.end_headers()
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("transferMode.dlna.org", "Streaming")
            self.send_header("ContentFeatures.dlna.org", content_features)
            self.end_headers()
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

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
    """Listens for UPnP M-SEARCH and sends ssdp:alive on startup + every 15 min."""

    # All UPnP notification types this server advertises
    _NT_TYPES = [
        "upnp:rootdevice",
        f"uuid:{DEVICE_UUID}",
        "urn:schemas-upnp-org:device:MediaServer:1",
        "urn:schemas-upnp-org:service:ContentDirectory:1",
    ]

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

        # Announce on startup so clients discover us immediately (no M-SEARCH needed)
        self._send_alive()

        alive_interval = 900  # 15 minutes, re-announce keepalive
        ticks = 0

        while not self._stop_event.is_set():
            try:
                data, addr = self._sock.recvfrom(1024)
                msg = data.decode("utf-8", errors="replace")
                if "M-SEARCH" in msg:
                    self._handle_msearch(msg, addr)
            except socket.timeout:
                ticks += 1
                if ticks >= alive_interval:
                    self._send_alive()
                    ticks = 0
                continue
            except Exception as e:
                logger.debug("SSDP recv error: %s", e)

    def _location(self) -> str:
        return f"http://{self.local_ip}:{self.http_port}/dlna/description.xml"

    def _send_alive(self):
        """Send ssdp:alive NOTIFY for all advertised NT types."""
        for nt in self._NT_TYPES:
            usn = (
                f"uuid:{DEVICE_UUID}"
                if nt == f"uuid:{DEVICE_UUID}"
                else f"uuid:{DEVICE_UUID}::{nt}"
            )
            notify = (
                "NOTIFY * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{self.ssdp_port}\r\n"
                "CACHE-CONTROL: max-age=1800\r\n"
                f"LOCATION: {self._location()}\r\n"
                f"NT: {nt}\r\n"
                "NTS: ssdp:alive\r\n"
                "SERVER: MediaStream/1.0 UPnP/1.0\r\n"
                f"USN: {usn}\r\n"
                "\r\n"
            )
            try:
                self._sock.sendto(notify.encode(), (SSDP_ADDR, self.ssdp_port))
            except Exception as e:
                logger.debug("SSDP alive send error: %s", e)

    def _handle_msearch(self, msg: str, addr):
        """Respond to M-SEARCH for any of our advertised types (or ssdp:all)."""
        search_all = "ssdp:all" in msg
        for nt in self._NT_TYPES:
            if search_all or nt in msg:
                self._send_response(nt, addr)
                if not search_all:
                    break  # respond once with the matching type

    def _send_response(self, st: str, addr):
        from email.utils import formatdate
        usn = (
            f"uuid:{DEVICE_UUID}"
            if st == f"uuid:{DEVICE_UUID}"
            else f"uuid:{DEVICE_UUID}::{st}"
        )
        response = (
            "HTTP/1.1 200 OK\r\n"
            "CACHE-CONTROL: max-age=1800\r\n"
            f"DATE: {formatdate(usegmt=True)}\r\n"
            f"LOCATION: {self._location()}\r\n"
            "SERVER: MediaStream/1.0 UPnP/1.0\r\n"
            f"ST: {st}\r\n"
            f"USN: {usn}\r\n"
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
            f"Smart TV / BigScreen VR (Quest): auto-discovered as '{settings.dlna_friendly_name}'  |  "
            f"VLC/Kodi: open network → http://{local_ip}:{settings.dlna_http_port}/dlna/description.xml"
        ),
    }
