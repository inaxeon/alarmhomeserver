"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
cid_service.py:
This class parses CID messages from the alarms and formats them as
human readable emails.

For example if the alarm sends "001d94033eb3 18340101015E23A"

It would reult in an email:

Subject: "[Away Arm] by Web (Matt)"
Body:

Event: [Away Arm] by Web (Matt)
Recorded at: 21/09/2026 13:48:31
Original CID: [001d94033eb3 18340101015E23A]

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

import asyncio
import datetime
import logging
from typing import Optional

from alarmhomeserver.config import Config
from alarmhomeserver.interfaces import Panel
from alarmhomeserver.models.alarm import Device, DeviceType, PanelType, PanelUser
from alarmhomeserver.models.cid_descriptions import CidMessage, EventType, EventTypeHelper
from alarmhomeserver.services.email_service import EmailService
from alarmhomeserver.services.panel_manager import PanelManager

logger = logging.getLogger(__name__)


class CidService:
    def __init__(self, app_settings: Config, panel_manager: PanelManager, email_service: EmailService):
        self._app_settings = app_settings
        self._panel_manager = panel_manager
        self._email_service = email_service
        self._server: Optional[asyncio.base_events.Server] = None
        self._last_event = ""

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, host="0.0.0.0", port=self._app_settings.cid_port)
        logger.info("Started CID listener on TCP port %s", self._app_settings.cid_port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        logger.info("CID listener stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        logger.info("CID client connected")
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break

                raw_message = data.decode("ascii", errors="replace").strip()

                try:
                    logger.info("Raw CID message: %s received at: %s", raw_message, datetime.datetime.now())
                    parsed = self._try_parse_cid_message(raw_message)
                    if parsed:
                        await self._process_cid_message(parsed, raw_message)
                    else:
                        raise ValueError("Failed to parse CID message")
                except Exception:
                    logger.exception("An error occurred processing a message")

                writer.write(b"\x06")  # ACK
                await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            logger.info("CID client disconnected.")

    def _try_parse_cid_message(self, message: str) -> Optional[CidMessage]:
        message = message.strip("\r\n[]")
        parts = message.split(" ")

        if len(message) < 14:
            return None

        try:
            return CidMessage(
                account_number=parts[0],
                event_code=int(parts[1][2:6]),
                partition=int(parts[1][6:8]),
                zone=int(parts[1][8:11]),
            )
        except Exception:
            logger.exception("An error occurred parsing a CID message")
            return None

    async def _process_cid_message(self, message: CidMessage, original: str) -> None:
        panel: Optional[Panel] = None
        if message.account_number.isdigit():
            panel = self._panel_manager.get_panel_by_account_number(int(message.account_number))
        else:
            panel = self._panel_manager.get_panel(message.account_number)

        if panel is None:
            logger.warning("Dropping CID message from account ID: %s. Not known by this service.", message.account_number)
            return

        if panel.type == PanelType.ML_yaukoz:
            await self._process_for_ml_alarm(message, original, panel)

        if panel.type in (PanelType.CTC_XMPP, PanelType.CTC_Polling):
            await self._process_for_ctc_alarm(message, original, panel)

    async def _process_for_ml_alarm(self, message: CidMessage, original: str, panel: Panel) -> None:
        device = self._get_device_by_index(panel, message.zone)
        try:
            event_type = EventType(message.event_code)
        except ValueError:
            logger.warning("Unknown CID event code: %s", message.event_code)
            return

        if EventTypeHelper.is_ignored(event_type):
            return

        description = EventTypeHelper.get_description(event_type)
        preposition = EventTypeHelper.get_preposition(event_type)
        subject = f"[{description}]"

        if preposition is not None:
            subject += f" {preposition} "

            if event_type == EventType.DisarmByKeypad:
                keypad = self._get_first_device_by_type(panel, DeviceType.Keypad)
                subject += keypad.name if keypad else "Unknown keypad"
                user = self._get_user_by_index(panel, message.zone)
                if user:
                    subject += f" ({user.name})"
            elif event_type in (EventType.ArmByApp, EventType.ArmByPanel, EventType.HomeArmByApp, EventType.DisarmByApp):
                subject += "Web"
                user = self._get_user_by_index(panel, panel.last_mode_change_user, no_default=True)
                if user:
                    subject += f" ({user.name})"
            elif event_type == EventType.HomeArmGeneral:
                # "small yaukoz" only sends this for Home Arm. "Zone" can be either a user or a device -
                # there's no way to know which, so make a best-effort guess like the original.
                user = self._get_user_by_index(panel, message.zone, no_default=True)
                if user:
                    subject += f"App ({user.name})"
                else:
                    subject += device.name if device else ""
            else:
                subject += device.name if device else ""

        await self._send_parsed_message(original, subject, event_type, panel)

    async def _process_for_ctc_alarm(self, message: CidMessage, original: str, panel: Panel) -> None:
        device = self._get_device_by_index(panel, message.zone)
        try:
            event_type = EventType(message.event_code)
        except ValueError:
            logger.warning("Unknown CID event code: %s", message.event_code)
            return

        # No such thing as a Home Arm event - instead look at the partition parameter.
        if event_type == EventType.ArmByPanel and (message.partition & 1) == 1:
            event_type = EventType.HomeArmByApp

        description = EventTypeHelper.get_description(event_type)
        preposition = EventTypeHelper.get_preposition(event_type)
        subject = f"[{description}]"

        if preposition is not None:
            subject += f" {preposition} "

            if event_type == EventType.DisarmByKeypad:
                keypad = self._get_first_device_by_type(panel, DeviceType.Keypad)
                subject += keypad.name if keypad else "Unknown keypad"
                user = self._get_user_by_index(panel, message.zone)
                if user:
                    subject += f" ({user.name})"
            elif event_type in (EventType.ArmByApp, EventType.ArmByPanel, EventType.HomeArmByApp, EventType.DisarmByApp):
                subject += "Web"
                user = self._get_user_by_index(panel, panel.last_mode_change_user, no_default=True)
                if user:
                    subject += f" ({user.name})"
            else:
                subject += device.name if device else ""

        await self._send_parsed_message(original, subject, event_type, panel)

    async def _send_parsed_message(self, original: str, subject: str, event_type: EventType, panel: Panel) -> None:
        body = f"Event: {subject}\nRecorded at: {datetime.datetime.now()}\nOriginal CID: {original}\n"

        logger.info("Alarm Event: %s", subject)

        if subject != self._last_event:
            logger.info("Sending email to all...")
            await self._email_service.send_email(panel, subject, body)
            logger.info("Emails sent.")
        else:
            logger.warning("Skipping duplicate event: %s", subject)

        self._last_event = subject

    def _get_user_by_index(self, panel: Optional[Panel], index: int, no_default: bool = False) -> Optional[PanelUser]:
        default = PanelUser(index=index, latch=False, name=f"User {index}", pin="0000")
        if panel is None:
            return default

        user = next((u for u in panel.users if u.index == index), None)
        if no_default:
            return user
        return user or default

    def _get_device_by_index(self, panel: Optional[Panel], index: int) -> Device:
        default = Device(index=index, name=f"Device {index}", type=DeviceType.Unknown)
        if panel is None:
            return default
        return next((d for d in panel.devices if d.index == index), None) or default

    def _get_first_device_by_type(self, panel: Optional[Panel], device_type: DeviceType) -> Device:
        default = Device(index=0, name=device_type.name, type=device_type)
        if panel is None:
            return default
        return next((d for d in panel.devices if d.type == device_type), None) or default
