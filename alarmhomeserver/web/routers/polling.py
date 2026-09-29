"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
polling.py:
Entry endpoint for legacy "polling" alarms i.e. CTC-1735
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

from fastapi import APIRouter, Request, Response

from alarmhomeserver.transports.http_polling_transport import HttpPollingTransport

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/polling", tags=["polling"])

@router.post("/receivexml")
async def receive_xml(request: Request):
    body = (await request.body()).decode("utf-8", errors="replace")

    xml = HttpPollingTransport.clean_xml(body)
    if not xml:
        logger.warning("Received empty or non-XML polling request body: %s", body)
        return Response(status_code=400)

    mac = HttpPollingTransport.extract_mac_address(xml)
    if not mac:
        logger.warning("Rejected a poll with no 'mac' attribute in its body: %s", xml)
        return Response(status_code=400)

    remote_ip = request.client.host if request.client else None
    panel_manager = request.app.state.panel_manager
    transport = panel_manager.get_or_create_http_polling_alarm(mac, xml, remote_ip)
    transport.ip_address = remote_ip

    response_xml = transport.deliver(xml)
    return Response(content=response_xml, media_type="text/xml")
