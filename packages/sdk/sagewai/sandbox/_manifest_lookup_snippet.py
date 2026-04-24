# This file is appended by release-sandbox.yml to the auto-generated
# image_manifest.py. Keep imports here minimal (re is enough).

_IMAGE_REF_RE = re.compile(
    r"^ghcr\.io/sagewai/sandbox-(?P<variant>[a-z][a-z0-9-]*):[^@]+$"
)


def lookup_digest(image_ref: str) -> str | None:
    """Return the pinned digest for ``image_ref`` or None if it is BYO.

    Matches only tag-form refs under the sagewai org (e.g.
    ``ghcr.io/sagewai/sandbox-base:0.1.5``). Callers that pass
    digest-form refs (``@sha256:...``) should skip the lookup.
    """
    match = _IMAGE_REF_RE.match(image_ref)
    if match is None:
        return None
    variant = match.group("variant")
    return PINNED_DIGESTS.get(variant)
