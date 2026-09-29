"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
panel.py:
Endpoint for media uploads.
When a PIR camera takes a photo or video the panel will upload it here.
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

import logging
import re

from datetime import datetime
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/media", tags=["media"])

_MAC_RE = re.compile(r"^[0-9A-Fa-f]{12}$")
_DEVICE_RE = re.compile(r"^Z[0-9][0-9]")

@router.post("/upload")
async def upload(request: Request):
    form = await request.form()
    files = [v for v in form.values() if hasattr(v, "filename") and v.filename]

    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    media_service = request.app.state.media_service

    for file in files:
        logger.info("Received media %s in field %s", file.filename, file.headers.get("content-disposition"))

        parts = file.filename.split("_")
        if len(parts) < 4 or not _MAC_RE.match(parts[0]):
            logger.error(
                "Prefix format for uploaded file is incorrect. On the Media Upload page please set the prefix to "
                "the MAC address formatted i.e. 001d94123456"
            )
            continue

        if not _DEVICE_RE.match(parts[1]):
            logger.error("Device format for uploaded file is incorrect. Expecting 'Zxx'")
            continue

        data = await file.read()

        try:
            panel_timestamp = datetime.strptime(f"{parts[2]} {parts[3].split('.')[0]}", "%Y-%m-%d %H%M%S")
        except ValueError:
            logger.error("Timestamp format for uploaded file '%s' is incorrect. Expecting 'yyyy-MM-dd HHmmss'", file.filename)
            continue

        await media_service.process_incoming_media(parts[0].lower(), parts[1], panel_timestamp, data)

    return {}
