"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
http_polling_transport.py:
Transport wrapper for "polling" alarms i.e. CTC-1735
This class converts the polling paradigm to the blocking method used by XMPP.
i.e. normally for XMPP we can send a command, block and wait for the result.
for polling alarms we have to wait for the panel to hit the HTTP polling
endpoint to interact with it. With polling times at a minimum of around
30 seconds, interaction with an alarm through this interface is SLOW.
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
import html
import logging
import re
import threading
import time
import urllib.parse
from typing import Callable, Optional

_MAC_ATTR_RE = re.compile(r'<mac value="([^"]+)"')

logger = logging.getLogger(__name__)


class HttpPollingTransport:
    def __init__(self, mac_address: str, log_xml_polling: bool = False):
        self._mac_address = mac_address
        self._log_xml_polling = log_xml_polling

        self._lock = threading.Lock()
        self._outgoing_command_xml: Optional[str] = None
        self._pending_command_id: Optional[str] = None
        self._awaiting_reply_command_id: Optional[str] = None
        self._delivery_signal: Optional[threading.Event] = None
        self._reply_signal: Optional[threading.Event] = None
        self._reply: Optional[str] = None
        self._command_gate = threading.Semaphore(1)

        self.ip_address: Optional[str] = None
        self.last_poll_at: datetime.datetime = datetime.datetime.utcnow()
        self.on_message_received: Optional[Callable[[str, bool], None]] = None

    def get_mac_address(self) -> str:
        return self._mac_address

    def set_message_received(self, callback: Callable[[str, bool], None]) -> None:
        self.on_message_received = callback

    @staticmethod
    def clean_xml(value: Optional[str]) -> str:
        """Extracts pure XML from raw HTTP POST bodies (stripping form-encoded prefixes,
        URL-decoding, HTML-entity decoding, and BOMs)."""
        if not value or not value.strip():
            return ""

        s = value.strip().strip("\ufeff\u200b")

        if "%3c" in s.lower():
            s = urllib.parse.unquote(s)

        if "&lt;" in s.lower():
            s = html.unescape(s)

        first_lt = s.find("<")
        last_gt = s.rfind(">")
        if first_lt >= 0 and last_gt > first_lt:
            s = s[first_lt : last_gt + 1]

        return s

    @staticmethod
    def extract_mac_address(polling_xml: str) -> Optional[str]:
        cleaned = HttpPollingTransport.clean_xml(polling_xml)
        match = _MAC_ATTR_RE.search(cleaned)
        return match.group(1).replace(":", "").lower() if match else None

    def deliver(self, incoming_xml: str) -> str:
        # Called by the HTTP endpoint for each inbound poll. Returns the XML to send back as the
        # HTTP response body: either a queued outbound command, or an empty ack if none pending.
        self.last_poll_at = datetime.datetime.utcnow()

        with self._lock:
            self._log_xml_line("<-", incoming_xml)

            # A poll body correlates to whatever command was delivered in the PREVIOUS poll.
            if self._awaiting_reply_command_id is not None and self._reply_signal is not None:
                self._reply = incoming_xml
                self._reply_signal.set()
                self._awaiting_reply_command_id = None
                self._reply_signal = None
            else:
                # Nothing was waiting for this reply - it's the alarm reporting state on its own.
                if self.on_message_received:
                    self.on_message_received(incoming_xml, True)

            if self._outgoing_command_xml is not None:
                to_send = self._outgoing_command_xml
                self._outgoing_command_xml = None
                self._awaiting_reply_command_id = self._pending_command_id
                self._pending_command_id = None
                if self._delivery_signal is not None:
                    self._delivery_signal.set()
                self._log_xml_line("->", to_send)
                return to_send

            ack = self._build_empty_polling_ack()
            self._log_xml_line("->", ack)
            return ack

    def send_command(
        self, body: str, context_handle: str, timeout_seconds: float, cancel_event: Optional[threading.Event] = None
    ) -> Optional[str]:
        # On this transport, timeouts must account for the panel's polling interval - override
        # normal shorter XMPP timeouts with a minimum polling timeout of 3 minutes.
        effective_timeout = max(timeout_seconds, 180.0)
        deadline = time.monotonic() + effective_timeout

        # Only one command is ever in flight - anything arriving while another is pending blocks.
        if not self._command_gate.acquire(timeout=effective_timeout):
            logger.warning("Timed out waiting for another command to finish before sending to '%s'", self._mac_address)
            return None

        try:
            delivery_signal = threading.Event()
            reply_signal = threading.Event()

            with self._lock:
                self._outgoing_command_xml = body
                self._pending_command_id = context_handle
                self._delivery_signal = delivery_signal
                self._reply_signal = reply_signal

            remaining = max(0.0, deadline - time.monotonic())
            if not delivery_signal.wait(remaining):
                logger.warning("Timed out waiting for a poll to deliver a command to '%s'", self._mac_address)
                return None

            if cancel_event is not None and cancel_event.is_set():
                return None

            remaining = max(0.0, deadline - time.monotonic())
            if not reply_signal.wait(remaining):
                logger.warning("Timed out waiting for '%s' to report the result of a command", self._mac_address)
                return None

            return self._reply
        finally:
            # Always release this command's claim on the single in-flight slot - otherwise a
            # timed-out command leaves the transport permanently rejecting the next one.
            with self._lock:
                if self._pending_command_id == context_handle:
                    self._pending_command_id = None
                    self._outgoing_command_xml = None
                if self._awaiting_reply_command_id == context_handle:
                    self._awaiting_reply_command_id = None
            self._command_gate.release()

    def stop(self) -> None:
        pass

    @staticmethod
    def _build_empty_polling_ack() -> str:
        return '<?xml version="1.0" encoding="ISO-8859-1"?><polling><result value="1"/></polling>'

    def _log_xml_line(self, direction: str, xml: str) -> None:
        if self._log_xml_polling:
            logger.info("%s [%s]: %s", direction, self._mac_address, xml)
