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


def lookup_variant(image_ref: str) -> "SandboxImageVariant | None":
    """Return the variant for a known Sagewai-published image, or None for BYO.

    Matches only tag-form refs under the sagewai org (e.g.
    ``ghcr.io/sagewai/sandbox-base:0.1.5``). Digest-form refs (``@sha256:...``)
    are treated as outside scope and return None — callers that pass
    digest-form refs should skip the lookup and trust the ref directly.

    Returns None when:
      - ref does not start with ``ghcr.io/sagewai/sandbox-``
      - the variant segment is not a known SandboxImageVariant enum value
      - the variant IS a known enum value but not present in the current
        SDK's PINNED_DIGESTS (pre-release state or partial release)
    """
    prefix = "ghcr.io/sagewai/sandbox-"
    if not image_ref.startswith(prefix):
        return None
    rest = image_ref[len(prefix):]
    # Require tag-form: variant + ':' + tag
    if ":" not in rest:
        return None
    variant_name = rest.split(":", 1)[0]
    try:
        variant = SandboxImageVariant(variant_name)
    except ValueError:
        return None
    if variant.value not in PINNED_DIGESTS:
        return None
    return variant
