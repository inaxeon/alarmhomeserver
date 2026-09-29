"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
config.py:
Persistence for alarm parameters
--------------------------------------------------------------------------------
This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; If not, see <http://www.gnu.org/licenses/>.
--------------------------------------------------------------------------------
"""
from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path
from typing import Optional


@dataclasses.dataclass
class PanelSettings:
    friendly_name: Optional[str] = None
    account_number: Optional[int] = None
    emails_from_address: Optional[str] = None
    emails_from_name: Optional[str] = None
    emails_to: Optional[str] = None
    media_save_path: Optional[str] = None
    email_media: Optional[bool] = None

    @staticmethod
    def from_dict(data: dict) -> "PanelSettings":
        return PanelSettings(
            friendly_name=data.get("friendly_name"),
            account_number=data.get("account_number"),
            emails_from_address=data.get("emails_from_address"),
            emails_from_name=data.get("emails_from_name"),
            emails_to=data.get("emails_to"),
            media_save_path=data.get("media_save_path"),
            email_media=data.get("email_media"),
        )


@dataclasses.dataclass
class Config:
    panels: dict[str, PanelSettings] = dataclasses.field(default_factory=dict)
    web_port: int = 8085
    web_username: str = ""
    web_password: str = ""
    polling_port: int = 0
    media_port: int = 8760
    polling_timeout_minutes: int = 15
    smtp_server: str = ""
    smtp_port: int = 587
    smtp_use_tls: bool = True
    xmpp_port: int = 5222
    cid_port: int = 8765
    log_xmpp_streams: bool = False
    log_xml_polling: bool = False
    log_dir: str = "logs"
    log_file: str = "alarmhomeserver.log"
    log_level: str = "INFO"
    log_max_bytes: int = 524288
    log_backup_count: int = 5
    ssl_cert_file: str = "ssl/yale-home-system-cert.pem"
    ssl_key_file: str = "ssl/yale-home-system-key.pem"

    @staticmethod
    def load(path: str | Path) -> "Config":
        path = Path(path)
        with path.open("rb") as f:
            raw = tomllib.load(f)
        section = raw.get("settings", raw)

        panels = {
            mac.lower(): PanelSettings.from_dict(value)
            for mac, value in (section.get("panels") or {}).items()
        }

        settings = Config(
            panels=panels,
            web_port=section.get("web_port", 8085),
            web_username=section.get("web_username", ""),
            web_password=section.get("web_password", ""),
            polling_port=section.get("polling_port", 0),
            media_port=section.get("media_port", 8760),
            polling_timeout_minutes=section.get("polling_timeout_minutes", 15),
            smtp_server=section.get("smtp_server", ""),
            smtp_port=section.get("smtp_port", 587),
            smtp_use_tls=bool(section.get("smtp_use_tls", True)),
            xmpp_port=section.get("xmpp_port", 5222),
            cid_port=section.get("cid_port", 8765),
            log_xmpp_streams=bool(section.get("log_xmpp_streams", False)),
            log_xml_polling=bool(section.get("log_xml_polling", False)),
        )

        logging_section = raw.get("logging", {})
        settings.log_level = logging_section.get("level", settings.log_level)
        settings.log_dir = logging_section.get("directory", settings.log_dir)
        settings.log_file = logging_section.get("file_name", settings.log_file)
        settings.log_max_bytes = logging_section.get("max_bytes", settings.log_max_bytes)
        settings.log_backup_count = logging_section.get("backup_count", settings.log_backup_count)

        ssl_section = raw.get("ssl", {})
        settings.ssl_cert_file = ssl_section.get("cert_file", settings.ssl_cert_file)
        settings.ssl_key_file = ssl_section.get("key_file", settings.ssl_key_file)

        return settings
