"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
panel_finder.py:
Mimics Climax's windows "Finder" application which sends out broadcast UDP
messages to find all alarm hubs on a given subnet.
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
import socket
from typing import Optional

from alarmhomeserver.models.alarm import DiscoveredPanel

logger = logging.getLogger(__name__)

FINDER_PORT = 55030
POLL_INTERVAL_SECONDS = 10
LISTEN_WINDOW_SECONDS = 2

OBFUSCATION_KEY = (
    "A9er4n]Dsa[totn;himrroo/u&aecll]rhbLs.k,allguJ,aAibfxmaqScyd1/jnNetmask"
)[:64].encode("ascii")

_SEARCH_REQUEST = b"SEARCH /panel FINDER/1.0\r\n"


def _obfuscate(data: bytes) -> bytes:
    return bytes((~(b ^ OBFUSCATION_KEY[i & 0x3F])) & 0xFF for i, b in enumerate(data))


def _parse_finder_reply(text: str) -> Optional[dict[str, str]]:
    if not text.startswith("FINDER/1.0"):
        return None

    headers: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        headers[key.strip()] = value.strip()
    return headers


class PanelFinder:
    def __init__(self):
        self._discovered: dict[str, DiscoveredPanel] = {}
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    def get_discovered_panels(self) -> list[DiscoveredPanel]:
        return list(self._discovered.values())

    async def _poll_loop(self) -> None:
        try:
            while True:
                try:
                    await self._poll_once()
                except Exception:
                    logger.exception("Discovery poll failed")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass

    async def _poll_once(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setblocking(False)
        sock.bind(("0.0.0.0", 0))

        try:
            payload = _obfuscate(_SEARCH_REQUEST)
            await loop.sock_sendto(sock, payload, ("255.255.255.255", FINDER_PORT))

            deadline = loop.time() + LISTEN_WINDOW_SECONDS
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    data, addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 2048), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                self._handle_reply(data, addr[0])
        finally:
            sock.close()

    def _handle_reply(self, data: bytes, from_ip: str) -> None:
        try:
            decoded = _obfuscate(data).decode("ascii", errors="replace")
        except Exception:
            return

        headers = _parse_finder_reply(decoded)
        if headers is None:
            return

        logger.debug("FINDER reply from %s: %s", from_ip, headers)

        raw_mac = headers.get("MAC")
        if not raw_mac:
            return

        mac = raw_mac.replace(":", "").lower()

        panel = DiscoveredPanel(
            mac_address=mac,
            hw_version=headers.get("HWVer"),
            sw_version=headers.get("SWVer"),
            lan_type=headers.get("LanType"),
            ip_address=headers.get("IP") or from_ip,
            netmask=headers.get("Netmask"),
            gateway=headers.get("GW"),
            dns1=headers.get("DNS1"),
            dns2=headers.get("DNS2"),
            webs_port=headers.get("WebsPort"),
            last_seen=datetime.datetime.now(datetime.timezone.utc),
        )
        self._discovered[mac] = panel
