import yaml
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field


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
    smb_workgroup: str = _cfg.get("smb", {}).get("workgroup", "MEDIASTREAM")
    smb_server_string: str = _cfg.get("smb", {}).get("server_string", "MediaStream Server")
    smb_share_name: str = _cfg.get("smb", {}).get("share_name", "MediaFiles")
    smb_config_path: str = _cfg.get("smb", {}).get("config_path", "/etc/samba/smb.conf")

    dlna_enabled: bool = _cfg.get("dlna", {}).get("enabled", True)
    dlna_server_name: str = _cfg.get("dlna", {}).get("server_name", "MediaStream DLNA")
    dlna_port: int = _cfg.get("dlna", {}).get("port", 1900)
    dlna_http_port: int = _cfg.get("dlna", {}).get("http_port", 8200)
    dlna_friendly_name: str = _cfg.get("dlna", {}).get("friendly_name", "MediaStream")

    class Config:
        env_prefix = "MEDIASTREAM_"


settings = Settings()
