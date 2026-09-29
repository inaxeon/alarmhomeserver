"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
xmpp_server.py:
Manager for xmpp_transport instances
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
import ssl
from typing import Callable, Optional

from alarmhomeserver.transports.xmpp_transport import XmppTransport

logger = logging.getLogger(__name__)


class XmppServer:
    def __init__(self, port: int, log_streams: bool = False, ssl_context: Optional[ssl.SSLContext] = None):
        self._port = port
        self._log_streams = log_streams
        self._ssl_context = ssl_context
        self._server: Optional[asyncio.base_events.Server] = None
        self._tasks: set[asyncio.Task] = set()

        self.on_stream_created: Optional[Callable[[XmppTransport], None]] = None
        self.on_stream_closed: Optional[Callable[[XmppTransport], None]] = None
        self.on_auth_received: Optional[Callable[[XmppTransport, str, str], None]] = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, host="0.0.0.0", port=self._port)
        logger.info("Started XMPP server on TCP port %s", self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for task in list(self._tasks):
            task.cancel()
        logger.info("XMPP server stopped")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        logger.info("New XMPP connection from %s", peer[0] if peer else "unknown")

        loop = asyncio.get_running_loop()
        transport = XmppTransport(loop, self._log_streams, self._ssl_context)
        transport.on_stream_closed = self.on_stream_closed
        transport.on_auth_received = self.on_auth_received

        if self.on_stream_created:
            self.on_stream_created(transport)

        task = asyncio.create_task(transport.start(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
