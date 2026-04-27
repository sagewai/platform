# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai artifact destination resolver — Plan ART.

Formalises where Mode 3+ workflow outputs land (GitHub repo / S3
bucket / host-mounted local path) and how the upload subprocess
inside the sandbox reads its credentials from the Sealed-injected
env.

See docs/superpowers/specs/2026-04-27-plan-art-artifact-destination-design.md.

Uploaders register at import time. The default set (GitHub, S3,
Local) is wired up here. Future destination types are additive — a
new uploader registers itself and the type literal extends.
"""

from sagewai.artifacts.github_uploader import GitHubUploader
from sagewai.artifacts.local_uploader import LocalUploader
from sagewai.artifacts.refs import register_uploader
from sagewai.artifacts.s3_uploader import S3Uploader

# Register uploaders at import. Future destination types add their own
# registration here without forcing a refactor of consumers.
register_uploader(GitHubUploader())
register_uploader(S3Uploader())
register_uploader(LocalUploader())
