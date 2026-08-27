# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Public API for the software Work profile."""

from sagewai.work.profiles.software.cloudflare import (
    CloudflareDeliveryConfig,
    CloudflareDeliveryControlProbe,
    cloudflare_delivery_preconditions,
    cloudflare_version_digest,
)
from sagewai.work.profiles.software.cloudflare_adapter import (
    CloudflareDeploymentReference,
    CloudflareDocsAdapterConfig,
    CloudflareDocsDeploymentProvider,
    CloudflareDocsObservationProvider,
    CloudflareDocsReleaseProvider,
    ProcessRunner,
    cloudflare_static_asset_digest,
)
from sagewai.work.profiles.software.cloudflare_flow import (
    CloudflareDocsDeliveryFlow,
    CloudflareDocsDeliveryPolicy,
    CloudflareRolloutStep,
)
from sagewai.work.profiles.software.delivery import (
    BlastRadius,
    DeliveryActionDeniedError,
    DeliveryApprovalRequiredError,
    DeliveryControlLostError,
    DeliveryControlProbe,
    DeliveryControlRequest,
    DeliveryLifecycle,
    Deployment,
    DeploymentProvider,
    HealthGate,
    HealthGateResult,
    HealthVerdict,
    ObservationProvider,
    ObservationResult,
    ReleaseCandidate,
    ReleaseProvider,
)
from sagewai.work.profiles.software.github import (
    CatalogGitHubClient,
    GitHubIssue,
    GitHubIssueLifecycle,
    GitHubMergeResult,
    GitHubPullRequest,
    GitHubPullRequestState,
    GitHubWorkContext,
    WorktreeBranchPublisher,
    is_github_issue_url,
    require_merge_approval,
)
from sagewai.work.profiles.software.lifecycle import (
    SoftwareLifecycle,
    SoftwareStageOperator,
)
from sagewai.work.profiles.software.models import (
    SoftwareAttemptContext,
    SoftwareCapsuleContext,
    SoftwareContractContext,
    SoftwareRepairContext,
    SoftwareReviewContext,
    SoftwareReviewFindingContext,
    SoftwareVerificationCheck,
    SoftwareWorkspace,
    WorkspaceStaleError,
)
from sagewai.work.profiles.software.scm import (
    SoftwareWorktreeManager,
    workspace_diff,
)
from sagewai.work.profiles.software.verification import (
    SoftwareReadOnlyResultValidator,
    SoftwareResultValidator,
    SoftwareVerifier,
)

__all__ = [
    "BlastRadius",
    "CatalogGitHubClient",
    "CloudflareDeliveryConfig",
    "CloudflareDeliveryControlProbe",
    "CloudflareDeploymentReference",
    "CloudflareDocsAdapterConfig",
    "CloudflareDocsDeploymentProvider",
    "CloudflareDocsDeliveryFlow",
    "CloudflareDocsDeliveryPolicy",
    "CloudflareDocsObservationProvider",
    "CloudflareDocsReleaseProvider",
    "CloudflareRolloutStep",
    "ProcessRunner",
    "cloudflare_delivery_preconditions",
    "cloudflare_version_digest",
    "cloudflare_static_asset_digest",
    "DeliveryActionDeniedError",
    "DeliveryApprovalRequiredError",
    "DeliveryControlProbe",
    "DeliveryControlLostError",
    "DeliveryControlRequest",
    "DeliveryLifecycle",
    "Deployment",
    "DeploymentProvider",
    "GitHubIssue",
    "GitHubIssueLifecycle",
    "GitHubMergeResult",
    "GitHubPullRequest",
    "GitHubPullRequestState",
    "GitHubWorkContext",
    "HealthGate",
    "HealthGateResult",
    "HealthVerdict",
    "ObservationProvider",
    "ObservationResult",
    "ReleaseCandidate",
    "ReleaseProvider",
    "WorktreeBranchPublisher",
    "is_github_issue_url",
    "require_merge_approval",
    "SoftwareAttemptContext",
    "SoftwareCapsuleContext",
    "SoftwareContractContext",
    "SoftwareLifecycle",
    "SoftwareReadOnlyResultValidator",
    "SoftwareRepairContext",
    "SoftwareResultValidator",
    "SoftwareReviewContext",
    "SoftwareReviewFindingContext",
    "SoftwareStageOperator",
    "SoftwareVerificationCheck",
    "SoftwareVerifier",
    "SoftwareWorkspace",
    "SoftwareWorktreeManager",
    "WorkspaceStaleError",
    "workspace_diff",
]
