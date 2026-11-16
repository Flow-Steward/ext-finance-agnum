"""Project-scoped Agnum connection: one Agnum DB API address and one API key.

The specification gives no server address (``servers: [{"url": "/"}]``): each Agnum
installation serves its own API. The address is the API's root as its Swagger page
shows it without ``/swagger-ui/…`` — for example ``https://server.example.lt/agnumAPIv2``
— and the steps add ``/api/…`` to it. It must be https, a host name (no IP literal,
no localhost) with an optional port and path, and no credentials, query or fragment;
the SDK's pinned opener additionally refuses a host that resolves to a private or
reserved address, and redirects are never followed.

The key lives in a private field of a frozen dataclass whose ``repr`` never shows
it; it travels only in the ``X-API-KEY`` header and is never placed in a result, a
log line or an exception message.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit

from . import errors
from .errors import ExtensionError

CONNECTION_TYPE_ID = "agnum"
CONFIG_FIELDS: frozenset[str] = frozenset({"label", "base_url"})
SECRET_FIELDS: frozenset[str] = frozenset({"api_key"})
MAX_URL_LENGTH = 300

#: Shorter keys could not be removed from answers without mangling them.
MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 512
#: Printable ASCII without spaces: a header value that cannot be split.
_KEY_RE = re.compile(r"^[\x21-\x7e]+$")
_HOST_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_PATH_RE = re.compile(r"^(/[A-Za-z0-9._~-]+)*$")
#: Paths that are the documentation, or the API's own /api, rather than its root.
_NOT_ROOT = ("/swagger-ui", "/v3/api-docs", "/api")


@dataclass(frozen=True)
class Connection:
    """One validated Agnum connection."""

    connection_id: str
    base_url: str
    _api_key: str = field(repr=False)

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    def secret_forms(self) -> tuple[str, ...]:
        """Every way the key can appear in a text: as is and encoded."""
        key = self._api_key
        forms = {
            key,
            quote_plus(key, safe=""),
            quote(key, safe=""),
            json.dumps(key, ensure_ascii=False)[1:-1],
            html.escape(key, quote=True),
            html.escape(key, quote=False),
        }
        # Longest first, so a form that contains another is removed whole.
        return tuple(sorted((f for f in forms if f), key=len, reverse=True))

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"Connection(connection_id={self.connection_id!r})"

    __str__ = __repr__


def _invalid(message: str) -> ExtensionError:
    return errors.no_effect(errors.INVALID_CONNECTION, message)


def base_url_of(value: Any) -> str:
    """``https://<host>[:port][/path]`` without a trailing slash, or a refusal."""
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise _invalid(
            "Add the Agnum API address to this connection (for example "
            "https://server.example.lt/agnumAPIv2)"
        )
    if len(text) > MAX_URL_LENGTH:
        raise _invalid("The Agnum API address is too long")
    parts = urlsplit(text)
    if parts.scheme != "https":
        raise _invalid("The Agnum API address must start with https://")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise _invalid("The Agnum API address may not contain credentials, a query or a fragment")
    host = (parts.hostname or "").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise _invalid("The Agnum API address must be a host name, not an IP address")
    if host == "localhost" or host.endswith((".localhost", ".local")) or not _HOST_RE.match(host):
        raise _invalid("The Agnum API address is not a public host name")
    try:
        port = parts.port
    except ValueError:
        raise _invalid("The Agnum API address has an invalid port") from None
    path = parts.path.rstrip("/")
    if not _PATH_RE.fullmatch(path):
        raise _invalid("The Agnum API address path may hold only letters, digits and . _ ~ -")
    lowered = path.lower()
    if any(marker in lowered for marker in _NOT_ROOT[:2]) or lowered.endswith(_NOT_ROOT[2]):
        raise _invalid(
            "Give the API's root, without /swagger-ui, /v3/api-docs or /api at the end (for "
            "example https://server.example.lt/agnumAPIv2)"
        )
    return f"https://{host}" + (f":{port}" if port and port != 443 else "") + path


def _api_key(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise _invalid("Add the Agnum API key to this connection")
    if len(text) < MIN_KEY_LENGTH or len(text) > MAX_KEY_LENGTH or not _KEY_RE.fullmatch(text):
        raise _invalid("The API key is not usable: 8 to 512 printable characters without spaces")
    return text


def connection_from_payload(payload: Mapping[str, Any], *, connection_ref: str) -> Connection:
    """Validate the selected project connection before any outbound request."""
    action = payload.get("action")
    target = action.get("target") if isinstance(action, Mapping) else None
    connection = target.get("connection") if isinstance(target, Mapping) else None
    if not isinstance(connection, Mapping):
        raise _invalid("The selected connection is unavailable")
    connection_id = str(connection.get("connection_id") or "")
    if not connection_id or (connection_ref and connection_id != connection_ref):
        raise _invalid("The selected connection does not match connection_ref")
    connection_type = str(
        connection.get("connection_type_id") or connection.get("connection_type") or ""
    )
    if connection_type and connection_type != CONNECTION_TYPE_ID:
        raise _invalid("The selected connection is not supported")
    config = connection.get("config")
    config = config if isinstance(config, Mapping) else {}
    if set(config) - CONFIG_FIELDS:
        raise _invalid("The connection carries unsupported settings")
    secrets = connection.get("secrets")
    secrets = secrets if isinstance(secrets, Mapping) else {}
    if set(secrets) - SECRET_FIELDS:
        raise _invalid("The connection carries unsupported secrets")
    return Connection(
        connection_id=connection_id,
        base_url=base_url_of(config.get("base_url")),
        _api_key=_api_key(secrets.get("api_key")),
    )


__all__ = [
    "CONFIG_FIELDS",
    "CONNECTION_TYPE_ID",
    "SECRET_FIELDS",
    "Connection",
    "base_url_of",
    "connection_from_payload",
]
