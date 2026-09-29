"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
media_service.py:
A place to put code related to media files
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

import datetime
import logging
import re
from pathlib import Path
from typing import Optional

from alarmhomeserver.models.api_models import SavedMediaDto
from alarmhomeserver.services.email_service import EmailService
from alarmhomeserver.services.panel_manager import PanelManager

logger = logging.getLogger(__name__)

# Windows compatible
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\s]')

class MediaService:
    def __init__(self, panel_manager: PanelManager, email_service: EmailService):
        self._panel_manager = panel_manager
        self._email_service = email_service

    async def process_incoming_media(self, address: str, device: str, panel_timestamp: datetime.datetime, file: bytes) -> None:
        panel = self._panel_manager.get_panel(address)
        if panel is None:
            logger.info("Dropping incoming media from '%s'. Not known by this service.", address)
            return

        device_index_str = device.lstrip("Z").lstrip("0") or "0"
        dev = next((d for d in panel.devices if d.index == int(device_index_str)), None)
        if dev is None:
            logger.info("Dropping incoming media from '%s'. Device '%s' not known by this service.", address, device)
            return

        subject = f"Image from {dev.name}"

        if panel.backend_config.media_save_path:
            file_name = create_safe_jpeg_filename(dev.name or "image", panel_timestamp)
            path = Path(panel.backend_config.media_save_path)
            path.mkdir(parents=True, exist_ok=True)
            (path / file_name).write_bytes(file)

        if panel.backend_config.email_media:
            await self._email_service.send_email_with_image(panel, subject, f"Recorded at: {datetime.datetime.now()}", file)

    def get_saved_media(self, panel) -> list[SavedMediaDto]:
        path = panel.backend_config.media_save_path if panel.backend_config else None
        if not path or not Path(path).is_dir():
            return []

        results = [
            SavedMediaDto(file_name=f.name, taken_at=_parse_media_timestamp(f.name)) for f in Path(path).glob("*.jpg")
        ]
        results.sort(key=lambda m: m.taken_at or datetime.datetime.min, reverse=True)
        return results

    def try_get_saved_media_path(self, panel, file_name: str) -> Optional[str]:
        path = panel.backend_config.media_save_path if panel.backend_config else None
        if not path:
            return None

        # file_name comes from get_saved_media's listing, but guard against path traversal anyway.
        candidate = Path(path) / Path(file_name).name
        return str(candidate) if candidate.is_file() else None

    def delete_saved_media(self, panel, file_name: str) -> bool:
        full_path = self.try_get_saved_media_path(panel, file_name)
        if full_path is None:
            return False
        Path(full_path).unlink()
        return True


def create_safe_jpeg_filename(original_filename: str, when: datetime.datetime) -> str:
    name = Path(original_filename).stem
    name = _INVALID_FILENAME_CHARS.sub("_", name).strip("_.")
    if not name:
        name = "image"

    timestamp = when.strftime("%Y%m%d_%H%M%S")
    return f"media_{name}_{timestamp}.jpg"


def _parse_media_timestamp(file_name: str) -> Optional[datetime.datetime]:
    parts = Path(file_name).stem.split("_")
    if len(parts) < 2:
        return None

    timestamp = f"{parts[-2]}_{parts[-1]}"
    try:
        return datetime.datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
    except ValueError:
        return None
