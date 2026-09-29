"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
tls.py:
SSL context builder
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
import ssl
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def build_server_ssl_context(cert_file: Optional[str], key_file: Optional[str]) -> Optional[ssl.SSLContext]:
    if not cert_file or not key_file or not Path(cert_file).is_file() or not Path(key_file).is_file():
        logger.warning("SSL cert/key not found (cert=%s, key=%s) - XMPP-TLS will not be available", cert_file, key_file)
        return None

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.minimum_version = ssl.TLSVersion.TLSv1_1
    except (ValueError, ssl.SSLError):
        logger.warning("This system's OpenSSL doesn't support TLS 1.1")

    # The alarms only use SHA-1.
    context.set_ciphers("DEFAULT@SECLEVEL=0")
    context.load_cert_chain(cert_file, key_file)
    return context
