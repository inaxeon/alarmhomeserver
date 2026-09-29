"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
panel_manager.py:
Manager class for alarm instances.
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
import logging
import threading
from typing import Optional

from alarmhomeserver.config import Config
from alarmhomeserver.models.alarm import PanelBackendConfig
from alarmhomeserver.panels.base_panel import BasePanel
from alarmhomeserver.panels.ctc_panel import CtcPanel
from alarmhomeserver.panels.ml_panel import MlPanel
from alarmhomeserver.tls import build_server_ssl_context
from alarmhomeserver.transports.http_polling_transport import HttpPollingTransport
from alarmhomeserver.transports.xmpp_server import XmppServer
from alarmhomeserver.transports.xmpp_transport import XmppTransport

logger = logging.getLogger(__name__)


class PanelManager:
    def __init__(self, app_settings: Config):
        self._app_settings = app_settings
        ssl_context = build_server_ssl_context(app_settings.ssl_cert_file, app_settings.ssl_key_file)
        self._xmpp_server = XmppServer(app_settings.xmpp_port, app_settings.log_xmpp_streams, ssl_context)
        self._xmpp_server.on_stream_created = self._on_stream_created
        self._xmpp_server.on_stream_closed = self._on_stream_closed
        self._xmpp_server.on_auth_received = self._on_auth_received

        self._active_alarms: list[BasePanel] = []
        self._lock = threading.Lock()
        self._stale_check_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        await self._xmpp_server.start()
        self._stale_check_task = asyncio.create_task(self._stale_polling_check_loop())

    async def stop(self) -> None:
        if self._stale_check_task:
            self._stale_check_task.cancel()
        await self._xmpp_server.stop()

        with self._lock:
            alarms = list(self._active_alarms)
        for alarm in alarms:
            alarm.close_connection()

    async def _stale_polling_check_loop(self) -> None:
        interval = 60
        try:
            while True:
                await asyncio.sleep(interval)
                self._remove_stale_polling_alarms()
        except asyncio.CancelledError:
            pass

    def _remove_stale_polling_alarms(self) -> None:
        import datetime

        timeout = datetime.timedelta(minutes=self._app_settings.polling_timeout_minutes)
        stale = []

        with self._lock:
            for alarm in list(self._active_alarms):
                transport = alarm.transport
                if isinstance(transport, HttpPollingTransport) and datetime.datetime.utcnow() - transport.last_poll_at > timeout:
                    stale.append(alarm)
            for alarm in stale:
                self._active_alarms.remove(alarm)

        for alarm in stale:
            logger.warning(
                "Alarm '%s': Removed - no poll received for over %s minute(s)",
                alarm.identifier,
                self._app_settings.polling_timeout_minutes,
            )
            alarm.close_connection()

    def get_default_panel(self):
        with self._lock:
            return next((a for a in self._active_alarms if a.is_ready), None)

    def get_panel(self, identifier: Optional[str]):
        if not identifier:
            return self.get_default_panel()
        with self._lock:
            return next((a for a in self._active_alarms if a.is_ready and a.identifier.lower() == identifier.lower()), None)

    def get_panel_by_account_number(self, account_number: int):
        with self._lock:
            return next(
                (a for a in self._active_alarms if a.is_ready and a.backend_config.account_number == account_number), None
            )

    def get_panels(self):
        with self._lock:
            # Still-initialising alarms are hidden here - using one before its own initial data
            # load has finished would clash with it over the same single-command transport slot.
            return [a for a in self._active_alarms if a.is_ready]

    def get_or_create_http_polling_alarm(self, mac_address: str, initial_polling_xml: str, ip_address: Optional[str]):
        with self._lock:
            existing = next((a for a in self._active_alarms if a.identifier.lower() == mac_address.lower()), None)
            if existing is not None and isinstance(existing.transport, HttpPollingTransport):
                return existing.transport

            logger.info("New polling connection from %s (%s)", ip_address, mac_address)

            transport = HttpPollingTransport(mac_address, self._app_settings.log_xml_polling)
            alarm = CtcPanel(
                logging.getLogger(f"alarmhomeserver.panel.{mac_address}"),
                transport,
                self._parse_backend_config(mac_address),
                initial_polling_xml,
            )
            self._active_alarms.append(alarm)

        def initialise():
            if alarm.initialise():
                logger.info("Alarm '%s': Successfully initialised over HTTP polling transport", mac_address)
            else:
                logger.error("Alarm '%s': Failed to initialise over HTTP polling transport", mac_address)
                with self._lock:
                    if alarm in self._active_alarms:
                        self._active_alarms.remove(alarm)

        threading.Thread(target=initialise, daemon=True).start()
        return transport

    def _on_stream_closed(self, stream: XmppTransport) -> None:
        with self._lock:
            self._active_alarms = [a for a in self._active_alarms if a.transport is not stream]

    def _on_auth_received(self, stream: XmppTransport, address: str, credential: str) -> None:
        pass  # A new alarm is attempting to connect - nothing to do until StreamEstablished.

    def _on_stream_created(self, stream: XmppTransport) -> None:
        # Detection blocks on events waiting for the panel to talk, so it must run off the
        # event-loop thread.
        threading.Thread(target=self._detect_and_initialise, args=(stream,), daemon=True).start()

    def _detect_and_initialise(self, stream: XmppTransport) -> None:
        alarm = self._detect_and_create_alarm(stream)

        if alarm is None:
            logger.error("Alarm '%s': Unable to determine alarm protocol; closing connection", stream.get_mac_address())
            stream.stop()
            return

        if alarm.initialise():
            logger.info("Alarm '%s': Successfully initialised and ready", alarm.backend_config.friendly_name)

            with self._lock:
                existing = next(
                    (a for a in self._active_alarms if a.identifier.lower() == stream.get_mac_address().lower()), None
                )
                if existing is not None:
                    logger.warning("Alarm '%s': Killing ghosted connection", existing.backend_config.friendly_name)
                    existing.close_connection()
                self._active_alarms.append(alarm)
        else:
            logger.error("Alarm '%s': Failed to initialise within the allowed time", alarm.backend_config.friendly_name)
            alarm.close_connection()

    def _detect_and_create_alarm(self, stream: XmppTransport) -> Optional[BasePanel]:
        if not stream.wait_for_stream_established(10):
            logger.error("Alarm '%s': XMPP session was never established", stream.get_mac_address())
            return None

        first_ml_message, first_xml_message = stream.wait_for_first_message(3)

        mac = stream.get_mac_address() or ""

        if first_ml_message is not None:
            return MlPanel(
                logging.getLogger(f"alarmhomeserver.panel.{mac}"),
                stream,
                self._parse_backend_config(mac),
                first_ml_message,
            )

        if first_xml_message is not None:
            return CtcPanel(logging.getLogger(f"alarmhomeserver.panel.{mac}"), stream, self._parse_backend_config(mac), first_xml_message)

        # CTC Panels don't send anything after re-connection, so actively probe.
        probe_xml = CtcPanel.build_probe_xml(mac)
        response = stream.send_command(probe_xml, "0", 5)

        if response and 'action="getVer"' in response and "<result>1</result>" in response:
            return CtcPanel(logging.getLogger(f"alarmhomeserver.panel.{mac}"), stream, self._parse_backend_config(mac), response)

        return None

    def _parse_backend_config(self, mac: str) -> PanelBackendConfig:
        config = PanelBackendConfig(
            friendly_name="Alarm",
            send_emails_to=[],
            send_emails_from_address="alarm@alarmtest.com",
            send_emails_from_name="Alarm",
            email_media=True,
        )

        section = self._app_settings.panels.get(mac.lower())
        if section is None:
            logger.warning("Alarm '%s': Has no configuration section. Using defaults.", mac)
            return config

        if section.account_number is not None:
            config.account_number = section.account_number
        if section.friendly_name:
            config.friendly_name = section.friendly_name
        if section.emails_to:
            config.send_emails_to = section.emails_to.split(";")
        if section.emails_from_address:
            config.send_emails_from_address = section.emails_from_address
        if section.emails_from_name:
            config.send_emails_from_name = section.emails_from_name
        if section.media_save_path:
            config.media_save_path = section.media_save_path
        if section.email_media is not None:
            config.email_media = section.email_media

        return config
