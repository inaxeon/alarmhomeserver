"""
--------------------------------------------------------------------------------
Alarm Home Server
Reverse engineered server for Climax alarm systems
Matthew Millman (inaxeon@hotmail.com)
--------------------------------------------------------------------------------
email_service.py:
SMTP email sender
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
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from alarmhomeserver.config import Config

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self, app_settings: Config):
        self._app_settings = app_settings

    async def send_email(self, panel, subject: str, body: str) -> bool:
        recipients = panel.backend_config.send_emails_to
        if not recipients:
            logger.warning("No email recipients configured for '%s'", panel.identifier)
            return True

        message = MIMEText(body, "plain")
        message["Subject"] = subject
        message["From"] = f"{panel.backend_config.send_emails_from_name} <{panel.backend_config.send_emails_from_address}>"
        message["To"] = ", ".join(recipients)

        return await asyncio.to_thread(self._send, message, recipients)

    async def send_email_with_image(self, panel, subject: str, body: str, image: bytes) -> bool:
        recipients = panel.backend_config.send_emails_to
        if not recipients:
            return True

        message = MIMEMultipart("related")
        message["Subject"] = subject
        message["From"] = f"{panel.backend_config.send_emails_from_name} <{panel.backend_config.send_emails_from_address}>"
        message["To"] = ", ".join(recipients)

        html = f'<html><body>{body}<br /><br /><img src="cid:image1" alt="Image" /></body></html>'
        message.attach(MIMEText(html, "html"))

        image_part = MIMEImage(image, _subtype="jpeg", name="image.jpg")
        image_part.add_header("Content-ID", "<image1>")
        message.attach(image_part)

        return await asyncio.to_thread(self._send, message, recipients)

    def _send(self, message, recipients: list[str]) -> bool:
        try:
            with smtplib.SMTP(self._app_settings.smtp_server, self._app_settings.smtp_port) as smtp:
                if self._app_settings.smtp_use_tls:
                    smtp.starttls()
                smtp.sendmail(message["From"], recipients, message.as_string())
            return True
        except Exception:
            logger.exception("Failed to send email")
            return False
