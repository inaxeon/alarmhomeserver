"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
app.py:
wires the API routers, static frontend and auth middleware.
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
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from alarmhomeserver.config import Config
from alarmhomeserver.web.auth import BasicAuthMiddleware
from alarmhomeserver.web.routers import media, panel, polling

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

def create_app(settings: Config, panel_manager, panel_finder, media_service) -> FastAPI:
    app = FastAPI(title="Alarm Home Server")

    app.state.settings = settings
    app.state.panel_manager = panel_manager
    app.state.panel_finder = panel_finder
    app.state.media_service = media_service

    app.include_router(panel.router)
    app.include_router(media.router)
    app.include_router(polling.router)

    app.add_middleware(BasicAuthMiddleware, settings=settings)

    static_dir_existed_at_startup = STATIC_DIR.is_dir()

    if static_dir_existed_at_startup:
        file_count = sum(1 for _ in STATIC_DIR.rglob("*") if _.is_file())
        logger.info("Serving static frontend from '%s' (%d files)", STATIC_DIR, file_count)
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    else:
        logger.warning("Static frontend directory '%s' does not exist - the web UI will 404.", STATIC_DIR)

    @app.middleware("http")
    async def _warn_if_static_dir_vanished(request: Request, call_next) -> Response:
        response = await call_next(request)
        if (
            response.status_code == 404
            and static_dir_existed_at_startup
            and not STATIC_DIR.is_dir()
            and not request.url.path.startswith(("/panel", "/media", "/polling", "/docs", "/openapi.json", "/redoc"))
        ):
            logger.error("Static frontend directory '%s' missing", STATIC_DIR)
        return response

    return app

