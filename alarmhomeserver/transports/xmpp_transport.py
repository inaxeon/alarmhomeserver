"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
xmpp_transport.py:
A minimal XMPP server which mimics the XMPP server in Climax home server.
Built entirely from snooping the communications with the real server.
Does just enough to talk to the alarms and nothing more :)
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
import base64
import dataclasses
import html
import logging
import ssl
import threading
import uuid
import xml.parsers.expat
from typing import Callable, Optional

logger = logging.getLogger(__name__)

STREAM_NS = "http://etherx.jabber.org/streams"
CLIENT_NS = "jabber:client"

@dataclasses.dataclass
class _Element:
    tag: str
    attrib: dict
    text: str = ""
    children: list["_Element"] = dataclasses.field(default_factory=list)

    @property
    def local_name(self) -> str:
        return self.tag.rsplit(":", 1)[-1]

    def find(self, local_name: str) -> Optional["_Element"]:
        for c in self.children:
            if c.local_name == local_name:
                return c
        return None


class _StanzaParser:
    def __init__(self):
        self._parser = xml.parsers.expat.ParserCreate()
        self._parser.StartElementHandler = self._on_start
        self._parser.EndElementHandler = self._on_end
        self._parser.CharacterDataHandler = self._on_text
        self._stack: list[_Element] = []
        self.ready: list[_Element] = []
        self.stream_opened: Optional[_Element] = None
        self.stream_closed = False

    def feed(self, data: bytes) -> None:
        if not data:
            return
        try:
            self._parser.Parse(data, False)
        except xml.parsers.expat.ExpatError as ex:
            logger.warning("Ignoring malformed XML from panel: %s", ex)

    def _on_start(self, name: str, attrs: dict) -> None:
        element = _Element(tag=name, attrib=dict(attrs))
        if name.rsplit(":", 1)[-1] == "stream" and not self._stack:
            # Outer <stream:stream> - never closed until disconnect, dispatch immediately.
            self.stream_opened = element
            self.ready.append(element)
            return
        if self._stack:
            self._stack[-1].children.append(element)
        self._stack.append(element)

    def _on_text(self, data: str) -> None:
        if self._stack:
            self._stack[-1].text += data

    def _on_end(self, name: str) -> None:
        if name.rsplit(":", 1)[-1] == "stream" and self.stream_opened is not None and not self._stack:
            self.stream_closed = True
            return
        if self._stack:
            finished = self._stack.pop()
            if not self._stack:
                self.ready.append(finished)


@dataclasses.dataclass
class _PendingCommand:
    token: str
    event: threading.Event = dataclasses.field(default_factory=threading.Event)
    response: Optional[str] = None


class XmppTransport:
    def __init__(self, loop: asyncio.AbstractEventLoop, log_streams: bool = False, ssl_context: Optional[ssl.SSLContext] = None):
        self._loop = loop
        self._log_streams = log_streams
        self._ssl_context = ssl_context
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._needs_fresh_parser = False
        self._tls_active = False

        self._state = "init"
        self._stream_id = uuid.uuid4().hex[:10]
        self._message_id_prefix = uuid.uuid4().hex[:5]
        self._message_id = 0
        self._from_address = "security_admin@yale-home-system"
        self._client_mac_address: Optional[str] = None
        self._client_domain: Optional[str] = None
        self._pending_commands: list[_PendingCommand] = []
        self._pending_lock = threading.Lock()

        self.ip_address: Optional[str] = None
        self.on_message_received: Optional[Callable[[str, bool], None]] = None
        self.on_stream_established: Optional[Callable[["XmppTransport"], None]] = None
        self.on_stream_closed: Optional[Callable[["XmppTransport"], None]] = None
        self.on_auth_received: Optional[Callable[["XmppTransport", str, str], None]] = None

        self._established_event = threading.Event()
        self._first_message_event = threading.Event()
        self._first_ml_message: Optional[str] = None
        self._first_xml_message: Optional[str] = None

    def get_mac_address(self) -> Optional[str]:
        return self._client_mac_address

    def set_message_received(self, callback: Callable[[str, bool], None]) -> None:
        self.on_message_received = callback

    async def start(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        peer = writer.get_extra_info("peername")
        self.ip_address = peer[0] if peer else None

        parser = _StanzaParser()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                if self._log_streams:
                    logger.info("<- %s", _readable(data))
                parser.feed(data)
                while parser.ready:
                    element = parser.ready.pop(0)
                    try:
                        await self._process_stanza(element)
                    except Exception:
                        logger.exception("Error processing stanza from '%s'", self._client_mac_address)
                    if self._needs_fresh_parser:
                        # TLS was just negotiated - the client will send a brand new stream
                        # header over the encrypted channel; discard any old parser state.
                        parser = _StanzaParser()
                        self._needs_fresh_parser = False
                if parser.stream_closed:
                    break
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
            if self.on_stream_closed:
                self.on_stream_closed(self)

    def stop(self) -> None:
        if self._writer is not None:
            self._loop.call_soon_threadsafe(self._writer.close)

    # Outbound, called from panel worker threads

    def send_command(self, body: str, context_handle: str, timeout_seconds: float, cancel_event=None) -> Optional[str]:
        req = _PendingCommand(token=context_handle)
        with self._pending_lock:
            self._pending_commands.append(req)

        message_id = self._generate_message_id()
        xml_text = (
            f'<message to="{_xml_escape(self._client_address())}" id="{message_id}" type="chat" '
            f'from="{self._from_address}/Smack">'
            f"<body>{_xml_escape(body)}</body></message>"
        )

        asyncio.run_coroutine_threadsafe(self._send_text(xml_text), self._loop)

        req.event.wait(timeout_seconds)

        with self._pending_lock:
            if req in self._pending_commands:
                self._pending_commands.remove(req)

        if not req.response:
            logger.error("Alarm '%s': Command '%s' timed out or returned no result", self._client_mac_address, body)

        return req.response

    def wait_for_stream_established(self, timeout_seconds: float) -> bool:
        return self._established_event.wait(timeout_seconds)

    def wait_for_first_message(self, timeout_seconds: float) -> tuple[Optional[str], Optional[str]]:
        self._first_message_event.wait(timeout_seconds)
        return self._first_ml_message, self._first_xml_message

    # Stanza processing

    async def _process_stanza(self, element: _Element) -> None:
        local = element.local_name
        if local == "stream":
            await self._handle_stream(element)
        elif local == "auth":
            await self._handle_auth(element)
        elif local == "starttls":
            await self._handle_starttls()
        elif local == "presence":
            await self._handle_presence(element)
        elif local == "message":
            await self._handle_message(element)
        elif local == "iq":
            await self._handle_iq(element)
        else:
            logger.warning("Unhandled stanza type '%s' from panel", local)

    async def _handle_stream(self, element: _Element) -> None:
        to = element.attrib.get("to")
        if self._state in ("init", "ssl_negotiated"):
            if not to:
                raise ValueError("Client XMPP domain missing")
            self._client_domain = to

            # Don't advertise STARTTLS once already running inside TLS, or the panel will try
            # to renegotiate SSL over and over again.
            starttls_feature = "" if self._tls_active else '<starttls xmlns="urn:ietf:params:xml:ns:xmpp-tls"/>'

            features = (
                '<stream:features>'
                f'{starttls_feature}'
                '<mechanisms xmlns="urn:ietf:params:xml:ns:xmpp-sasl">'
                "<mechanism>PLAIN</mechanism><mechanism>ANONYMOUS</mechanism>"
                "<mechanism>SCRAM-SHA-1</mechanism><mechanism>CRAM-MD5</mechanism>"
                "<mechanism>DIGEST-MD5</mechanism></mechanisms>"
                '<compression xmlns="http://jabber.org/features/compress"><method>zlib</method></compression>'
                '<ver xmlns="urn:ietf:params:xml:ns:xmpp-features:rosterver"/>'
                '<register xmlns="http://jabber.org/features/iq-register"/>'
                "</stream:features>"
            )
            await self._send_stream_open(to, features)
            self._state = "pre_auth"
        elif self._state == "auth_complete":
            if self._writer is None:
                return

            starttls_feature = "" if self._tls_active else '<starttls xmlns="urn:ietf:params:xml:ns:xmpp-tls"/>'

            features = (
                '<stream:features>'
                '<compression xmlns="http://jabber.org/features/compress"><method>zlib</method></compression>'
                '<ver xmlns="urn:ietf:params:xml:ns:xmpp-features:rosterver"/>'
                '<bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"/>'
                '<session xmlns="urn:ietf:params:xml:ns:xmpp-session"><optional/></session>'
                '<sm xmlns="urn:xmpp:sm:2"/><sm xmlns="urn:xmpp:sm:3"/>'
                f'{starttls_feature}'
                '<mechanisms xmlns="urn:ietf:params:xml:ns:xmpp-sasl">'
                "<mechanism>PLAIN</mechanism><mechanism>ANONYMOUS</mechanism>"
                "<mechanism>SCRAM-SHA-1</mechanism><mechanism>CRAM-MD5</mechanism>"
                "<mechanism>DIGEST-MD5</mechanism></mechanisms>"
                '<register xmlns="http://jabber.org/features/iq-register"/>'
                "</stream:features>"
            )
            await self._send_stream_open(to or self._client_domain, features)
            self._state = "handshake"
        else:
            raise ValueError(f"Server not in correct state to receive 'stream': {self._state}")

    async def _handle_auth(self, element: _Element) -> None:
        if self._state != "pre_auth":
            raise ValueError(f"Server not in correct state to receive 'auth': {self._state}")

        value = element.text.strip()

        await self._send_text('<success xmlns="urn:ietf:params:xml:ns:xmpp-sasl"/>')

        data = base64.b64decode(value)
        auth_strings = [part.decode("utf-8", errors="replace") for part in data.split(b"\x00") if part]

        if len(auth_strings) < 2:
            raise ValueError(f"Invalid auth request received: {value}")

        logger.info("Alarm auth request: %s/%s", auth_strings[0], auth_strings[1])
        self._client_mac_address = auth_strings[0]
        if self.on_auth_received:
            self.on_auth_received(self, auth_strings[0], auth_strings[1])
        self._state = "auth_complete"

    async def _handle_starttls(self) -> None:
        await self._send_text('<proceed xmlns="urn:ietf:params:xml:ns:xmpp-tls"/>')

        if self._ssl_context is None or self._writer is None:
            logger.warning(
                "Panel '%s' requested STARTTLS but no server certificate is configured",
                self._client_mac_address,
            )
            return

        try:
            await self._writer.start_tls(self._ssl_context)
        except ssl.SSLError:
            logger.exception("TLS handshake failed for '%s'", self._client_mac_address)
            raise

        self._state = "ssl_negotiated"
        self._needs_fresh_parser = True
        self._tls_active = True
        logger.info("Alarm '%s': upgraded XMPP connection to TLS", self._client_mac_address)


    async def _handle_message(self, element: _Element) -> None:
        body_element = element.find("body")
        body = body_element.text if body_element else None

        if not body or not body.strip():
            logger.warning("Ignoring empty XMPP message body from '%s'", self._client_mac_address)
            return

        body = body.lstrip()

        if body.startswith("<?xml"):
            await self._handle_xml_message(body)
        else:
            await self._handle_ml_message(body)

    async def _handle_xml_message(self, body: str) -> None:
        with self._pending_lock:
            if len(self._pending_commands) == 1:
                req = self._pending_commands[0]
                req.response = body
                req.event.set()
                return

        if not self._first_message_event.is_set():
            self._first_xml_message = body
            self._first_message_event.set()

        if self.on_message_received:
            self.on_message_received(body, True)

    async def _handle_ml_message(self, body: str) -> None:
        end_token_index = body.find(":")
        if end_token_index < 0:
            logger.warning("Ignoring unrecognized XMPP message body from '%s': %s", self._client_mac_address, body)
            return

        token = body[:end_token_index]

        if len(token) == 32:
            with self._pending_lock:
                req = next((r for r in self._pending_commands if token.startswith(r.token)), None)
            if req:
                req.response = body[end_token_index + 1 :]
                req.event.set()
            return

        if not self._first_message_event.is_set():
            self._first_ml_message = body
            self._first_message_event.set()

        if self.on_message_received:
            self.on_message_received(body, False)

    async def _handle_iq(self, element: _Element) -> None:
        iq_type = element.attrib.get("type")
        iq_id = element.attrib.get("id", "")
        to = f"{self._client_address()}/panel"

        if element.find("query") is not None and iq_type == "get":
            await self._send_text(
                f'<iq type="result" id="{iq_id}" to="{_xml_escape(to)}">'
                '<query xmlns="jabber:iq:roster" ver="1279544689">'
                f'<item jid="{self._from_address}" name="security_admin" subscription="both" group="Panel"/>'
                "</query></iq>"
            )
        elif element.find("query") is not None and iq_type == "set":
            await self._send_text(f'<iq type="result" id="{iq_id}" to="{_xml_escape(to)}"/>')
        elif element.find("ping") is not None and iq_type == "get":
            await self._send_text(f'<iq type="result" id="{iq_id}" to="{_xml_escape(to)}"/>')
        elif element.find("bind") is not None and iq_type == "set":
            full_jid = f"{self._client_address()}/panel"
            await self._send_text(
                f'<iq type="result" id="{iq_id}" to="{self._client_domain}/{self._stream_id}">'
                f'<bind xmlns="urn:ietf:params:xml:ns:xmpp-bind"><jid>{_xml_escape(full_jid)}</jid></bind></iq>'
            )
        elif element.find("session") is not None and iq_type == "set":
            await self._send_text(f'<iq type="result" id="{iq_id}" to="{_xml_escape(to)}"/>')

    async def _handle_presence(self, element: _Element) -> None:
        presence_type = element.attrib.get("type", "")
        if presence_type in ("subscribe", ""):
            presence_id = uuid.uuid4().hex[:5].upper()
            to = f"{self._client_address()}/panel"
            await self._send_text(
                f'<presence id="{presence_id}" from="{self._from_address}/Smack" to="{_xml_escape(to)}">'
                '<c xmlns="http://jabber.org/protocol/caps" '
                'node="http://www.igniterealtime.org/projects/smack" ver="NfJ3flI83zSdUDzCEICtbypursw="/>'
                "</presence>"
            )
            iq_id = f"{uuid.uuid4().int % 1000:03d}-{uuid.uuid4().int % 10000000:07d}"
            await self._send_text(
                f'<iq type="set" id="{iq_id}" to="{_xml_escape(to)}">'
                '<query xmlns="jabber:iq:roster" ver="1279544689">'
                f'<item jid="{self._from_address}" subscription="both" group="panel"/>'
                "</query></iq>"
            )
            self._established_event.set()
            if self.on_stream_established:
                self.on_stream_established(self)

    # Helpers

    def _client_address(self) -> str:
        if not self._client_mac_address or not self._client_domain:
            raise ValueError("Unable to build client address. Missing MAC address or domain.")
        return f"{self._client_mac_address}@{self._client_domain}"

    def _generate_message_id(self) -> str:
        self._message_id += 1
        if self._message_id > 9999999:
            self._message_id = 1
        return f"{self._message_id_prefix}-{self._message_id:07d}"

    async def _send_stream_open(self, to: Optional[str], features_xml: str) -> None:
        open_tag = (
            f'<stream:stream xmlns:stream="{STREAM_NS}" xmlns="{CLIENT_NS}" '
            f'from="{_xml_escape(to or "")}" id="{self._stream_id}" xml:lang="en" version="1.0">'
        )
        await self._send_text(open_tag + features_xml)

    async def _send_text(self, text: str) -> None:
        if self._writer is None:
            return
        if self._log_streams:
            logger.info("-> %s", _readable(text))
        try:
            self._writer.write(text.encode("utf-8"))
            await self._writer.drain()
        except (ConnectionResetError, RuntimeError):
            pass


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _readable(value) -> str:
    # Unescape payloads for logging
    text = value.decode("utf-8", errors="replace") if isinstance(value, (bytes, bytearray)) else value
    return html.unescape(text)
