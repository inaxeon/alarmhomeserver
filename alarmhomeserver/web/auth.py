"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
auth.py:
HTTP Basic Auth middleware
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

import base64
import binascii
import hmac
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from alarmhomeserver.config import Config

logger = logging.getLogger(__name__)

MEDIA_PATH = "/media"
POLLING_PATH = "/polling"


class BasicAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Config):
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next):
        settings = self._settings
        local_port = (request.scope.get("server") or (None, None))[1]

        is_media_path = request.url.path.startswith(MEDIA_PATH)
        is_polling_path = request.url.path.startswith(POLLING_PATH)

        polling_shares_web_port = settings.polling_port > 0 and settings.polling_port == settings.web_port
        media_shares_web_port = settings.media_port > 0 and settings.media_port == settings.web_port

        if settings.polling_port > 0 and not polling_shares_web_port:
            is_polling_port = local_port == settings.polling_port
            if is_polling_port != is_polling_path:
                return PlainTextResponse(status_code=404, content="Not Found")
            if is_polling_port:
                return await call_next(request)
        elif is_polling_path:
            return await call_next(request)

        if settings.media_port > 0 and not media_shares_web_port:
            is_media_port = local_port == settings.media_port
            if is_media_port != is_media_path:
                return PlainTextResponse(status_code=404, content="Not Found")
            if is_media_port:
                return await call_next(request)
        elif is_media_path:
            return await call_next(request)

        if not settings.web_username:
            return await call_next(request)

        if not self._is_authenticated(request):
            client = request.scope.get("client")
            logger.warning(
                "Rejected unauthenticated %s %s from %s", request.method, request.url.path, client[0] if client else "?"
            )
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Alarm Home Server", charset="UTF-8"'},
            )

        return await call_next(request)

    def _is_authenticated(self, request: Request) -> bool:
        header = request.headers.get("Authorization")
        if not header or not header.lower().startswith("basic "):
            return False

        try:
            decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return False

        username, _, password = decoded.partition(":")
        return hmac.compare_digest(username, self._settings.web_username) and hmac.compare_digest(
            password, self._settings.web_password
        )
