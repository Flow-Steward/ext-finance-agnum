"""Workflow files: the PDF a step saves."""

from __future__ import annotations

from collections.abc import Mapping

from . import errors
from .context import Context
from .errors import ExtensionError

ARTIFACT_TIMEOUT_SECONDS = 120.0
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024

#: Must match contracts/artifact_policies.yaml (tests/reference/build_contracts.py).
DOWNLOAD_BINDING = "agnum_file"
DOWNLOAD_CONTENT_TYPE = "application/pdf"


def save(context: Context, data: bytes, *, binding_key: str, content_type: str, what: str) -> str:
    """Save ``data`` as a workflow file; its handle."""
    try:
        written = context.artifact_writer(
            dict(context.payload),
            [data],
            binding_key=binding_key,
            content_type=content_type,
            timeout_seconds=ARTIFACT_TIMEOUT_SECONDS,
        )
    except ExtensionError:
        raise
    except Exception:
        raise errors.no_effect(
            errors.ARTIFACT_OUTPUT_UNAVAILABLE, f"{what} could not be saved"
        ) from None
    handle = written.get("artifact_handle") if isinstance(written, Mapping) else None
    if not isinstance(handle, str) or not handle.strip():
        raise errors.no_effect(errors.ARTIFACT_OUTPUT_UNAVAILABLE, f"{what} could not be saved")
    return handle.strip()


__all__ = [
    "ARTIFACT_TIMEOUT_SECONDS",
    "DOWNLOAD_BINDING",
    "DOWNLOAD_CONTENT_TYPE",
    "MAX_DOWNLOAD_BYTES",
    "save",
]
