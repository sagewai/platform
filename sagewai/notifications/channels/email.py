# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SMTP email notification channel.

Uses ``aiosmtplib`` for async SMTP delivery. The library is optional —
if not installed, ``send()`` returns ``False`` with a clear log message.
"""

from __future__ import annotations

import logging
from typing import Any

from sagewai.notifications.channels.base import NotificationChannel

logger = logging.getLogger(__name__)

try:
    import aiosmtplib

    _HAS_AIOSMTPLIB = True
except ImportError:
    _HAS_AIOSMTPLIB = False


class SMTPChannel(NotificationChannel):
    """Send notifications as HTML emails via SMTP."""

    def __init__(
        self,
        *,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        use_tls: bool = True,
        from_address: str,
        to_addresses: list[str],
    ) -> None:
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.use_tls = use_tls
        self.from_address = from_address
        self.to_addresses = list(to_addresses)

    async def send(
        self,
        title: str,
        body: str,
        severity: str,
        metadata: dict[str, Any],
    ) -> bool:
        """Send an HTML email notification."""
        if not _HAS_AIOSMTPLIB:
            logger.error(
                "aiosmtplib is not installed. "
                "Install it with: pip install aiosmtplib"
            )
            return False

        if not self.to_addresses:
            logger.warning("SMTPChannel: no recipients configured")
            return False

        try:
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText

            severity_colors = {
                "info": "#2196F3",
                "warning": "#FF9800",
                "critical": "#F44336",
            }
            color = severity_colors.get(severity, "#2196F3")

            html = (
                f"<div style='font-family: sans-serif; max-width: 600px;'>"
                f"<div style='background: {color}; color: white; "
                f"padding: 12px 16px; border-radius: 4px 4px 0 0;'>"
                f"<strong>{severity.upper()}</strong></div>"
                f"<div style='padding: 16px; border: 1px solid #e0e0e0; "
                f"border-top: none; border-radius: 0 0 4px 4px;'>"
                f"<p>{body}</p>"
                f"</div></div>"
            )

            msg = MIMEMultipart("alternative")
            msg["Subject"] = title
            msg["From"] = self.from_address
            msg["To"] = ", ".join(self.to_addresses)
            msg.attach(MIMEText(body, "plain"))
            msg.attach(MIMEText(html, "html"))

            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user,
                password=self.smtp_password,
                start_tls=self.use_tls,
            )
            logger.info("Email sent: %s -> %s", title, self.to_addresses)
            return True
        except Exception as exc:
            logger.error("SMTPChannel send failed: %s", exc)
            return False
