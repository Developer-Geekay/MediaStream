import yaml
import os
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings


CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "./config/config.yaml"))


def load_yaml_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f)
    return {}


_cfg = load_yaml_config()


class Settings(BaseSettings):
    host: str = _cfg.get("app", {}).get("host", "0.0.0.0")
    port: int = _cfg.get("app", {}).get("port", 8080)
    secret_key: str = _cfg.get("app", {}).get("secret_key", "changeme")
    token_expire_minutes: int = _cfg.get("app", {}).get("token_expire_minutes", 1440)
    media_root: str = _cfg.get("media", {}).get("root_path", "./media")
    media_mappings: list = _cfg.get("media", {}).get("mappings", []) or []
    allowed_extensions: list = _cfg.get("media", {}).get("allowed_extensions", [])

    ftp_enabled: bool = _cfg.get("ftp", {}).get("enabled", True)
    ftp_host: str = _cfg.get("ftp", {}).get("host", "0.0.0.0")
    ftp_port: int = _cfg.get("ftp", {}).get("port", 2121)
    ftp_passive_start: int = _cfg.get("ftp", {}).get("passive_ports_start", 60000)
    ftp_passive_end: int = _cfg.get("ftp", {}).get("passive_ports_end", 60100)
    ftp_tls_enabled: bool = _cfg.get("ftp", {}).get("tls_enabled", True)
    ftp_tls_cert: str = _cfg.get("ftp", {}).get("tls_cert", "./certs/server.crt")
    ftp_tls_key: str = _cfg.get("ftp", {}).get("tls_key", "./certs/server.key")
    ftp_max_connections: int = _cfg.get("ftp", {}).get("max_connections", 50)
    ftp_max_per_ip: int = _cfg.get("ftp", {}).get("max_connections_per_ip", 5)

    smb_enabled: bool = _cfg.get("smb", {}).get("enabled", True)
    smb_host: str = _cfg.get("smb", {}).get("host", "0.0.0.0")
    smb_port: int = _cfg.get("smb", {}).get("port", 4450)
    smb_workgroup: str = _cfg.get("smb", {}).get("workgroup", "MEDIASTREAM")
    smb_share_name: str = _cfg.get("smb", {}).get("share_name", "MEDIAFILES")

    dlna_enabled: bool = _cfg.get("dlna", {}).get("enabled", True)
    dlna_friendly_name: str = _cfg.get("dlna", {}).get("friendly_name", "MediaStream")
    dlna_http_port: int = _cfg.get("dlna", {}).get("http_port", 8200)
    dlna_ssdp_port: int = _cfg.get("dlna", {}).get("ssdp_port", 1900)

    class Config:
        env_prefix = "MEDIASTREAM_"


settings = Settings()


def get_media_mappings() -> list[dict]:
    """
    Return the effective list of media mappings as {name, path: Path} dicts.

    Priority:
      1. Runtime JSON store (config/media_mappings.json) — managed via web UI
      2. config.yaml static list — fallback for deployments that prefer config files
      3. media_root — single-folder legacy mode
    """
    # 1. Runtime store (web-UI managed)
    from app.services.media_mappings import load_mappings
    mappings = load_mappings()
    if mappings:
        return mappings

    # 2. config.yaml static list
    raw = settings.media_mappings
    if raw:
        result = []
        for entry in raw:
            name = (entry.get("name") or "").strip()
            path_str = (entry.get("path") or "").strip()
            if name and path_str:
                result.append({"name": name, "path": Path(path_str).resolve()})
        if result:
            return result

    # 3. Fallback: treat media_root as a single mapping
    return [{"name": "media", "path": Path(settings.media_root).resolve()}]


def resolve_media_path(virtual: str) -> tuple[Optional[str], Optional[Path], Optional[Path]]:
    """
    Resolve a virtual path (e.g. "Movies/Action/film.mkv") to:
      (mapping_name, mapping_root, absolute_fs_path)

    Returns (None, None, None) when virtual is empty (caller handles virtual root).
    Raises ValueError on path-traversal attempts or unknown mapping names.
    """
    virtual = virtual.strip("/")
    if not virtual:
        return None, None, None

    parts = Path(virtual).parts
    mapping_name = parts[0]
    rest = Path(*parts[1:]) if len(parts) > 1 else Path(".")

    for m in get_media_mappings():
        if m["name"] == mapping_name:
            mapping_root = m["path"]
            target = (mapping_root / rest).resolve()
            if not str(target).startswith(str(mapping_root)):
                raise ValueError("Path traversal detected")
            return mapping_name, mapping_root, target

    raise ValueError(f"Mapping '{mapping_name}' not found")
