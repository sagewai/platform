# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pure decision-channel tests."""

from __future__ import annotations

import httpx

from sagewai.work.tasks.decisions import ChannelDeliveryError, channel_error_detail


def test_a_channel_failure_never_logs_the_endpoint() -> None:
    request = httpx.Request("POST", "https://hooks.slack.com/services/T/B/SECRET")
    status = httpx.HTTPStatusError(
        "Server error '500' for url 'https://hooks.slack.com/services/T/B/SECRET'",
        request=request,
        response=httpx.Response(500, request=request),
    )

    assert channel_error_detail(status) == "HTTPStatusError"
    assert "SECRET" not in channel_error_detail(status)
    assert (
        channel_error_detail(ChannelDeliveryError("slack_webhook webhook returned HTTP 500"))
        == "slack_webhook webhook returned HTTP 500"
    )
