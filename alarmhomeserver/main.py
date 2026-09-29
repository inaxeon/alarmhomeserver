#!/usr/bin/env python3
"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
main.py:
Server main startup
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
import sys
import uvicorn

from alarmhomeserver.config import Config
from alarmhomeserver.logging_setup import configure_logging
from alarmhomeserver.services.cid_service import CidService
from alarmhomeserver.services.email_service import EmailService
from alarmhomeserver.services.media_service import MediaService
from alarmhomeserver.services.panel_finder import PanelFinder
from alarmhomeserver.services.panel_manager import PanelManager
from alarmhomeserver.web.app import create_app

logger = logging.getLogger(__name__)


async def async_main(config_path: str) -> None:
    settings = Config.load(config_path)
    configure_logging(settings)

    logger.info("Starting Alarm Home Server")

    panel_manager = PanelManager(settings)
    panel_finder = PanelFinder()
    email_service = EmailService(settings)
    media_service = MediaService(panel_manager, email_service)
    cid_service = CidService(settings, panel_manager, email_service)

    app = create_app(settings, panel_manager, panel_finder, media_service)

    await panel_manager.start()
    await panel_finder.start()
    await cid_service.start()

    endpoints = _build_uvicorn_servers(app, settings)

    stop_event = asyncio.Event()
    startup_failed = False
    loop = asyncio.get_running_loop()

    try:
        import signal

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)
    except (NotImplementedError, AttributeError):
        pass

    async def _serve_or_warn(label: str, server: uvicorn.Server, critical: bool) -> None:
        nonlocal startup_failed
        try:
            await server.serve()
        except SystemExit:
            if critical:
                # The web UI is this service's only reason for existing on this port - if it can't
                # bind, running on regardless would just leave a silent, unreachable zombie process.
                logger.error(
                    "Could not start the %s endpoint on port %s - shutting down.", label, server.config.port
                )
                startup_failed = True
                stop_event.set()
        except Exception:
            logger.exception("The %s endpoint on port %s stopped unexpectedly", label, server.config.port)

    server_tasks = [
        asyncio.create_task(_serve_or_warn(label, server, port == settings.web_port))
        for label, server, port in endpoints
    ]

    await stop_event.wait()

    logger.info("Shutting down...")
    for _, server, _ in endpoints:
        server.should_exit = True
    await asyncio.gather(*server_tasks, return_exceptions=True)

    await cid_service.stop()
    await panel_finder.stop()
    await panel_manager.stop()

    if startup_failed:
        sys.exit(1)


def _build_uvicorn_servers(app, settings: Config) -> list[tuple[str, uvicorn.Server, int]]:
    # Start uvicorn on the various ports used by this service
    labels_by_port: dict[int, list[str]] = {}
    for label, port in (("web UI/API", settings.web_port), ("polling", settings.polling_port), ("media", settings.media_port)):
        if port > 0:
            labels_by_port.setdefault(port, []).append(label)

    endpoints = []
    for port in sorted(labels_by_port):
        config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level=settings.log_level.lower(), access_log=False)
        endpoints.append(("/".join(labels_by_port[port]), uvicorn.Server(config), port))
    return endpoints


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    asyncio.run(async_main(config_path))

if __name__ == "__main__":
    main()
