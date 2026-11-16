"""The only way this extension reaches Agnum.

Each documented operation is a REST call to ``<connection address>/api/…`` with the
method the specification gives: GET with query parameters, POST/PUT/PATCH with a
JSON body, DELETE with the ID in the path. The key travels only in the ``X-API-KEY``
header. Requests go out through the public Flow Steward extension SDK, which pins
DNS, verifies the connected peer, refuses redirects and enforces the timeout; they
are sent one at a time, never in parallel, and never retried.

Refusals carry an HTTP status and, by the specification, an RFC 7807 problem
(``title``, ``status``, ``detail``, ``message``, ``errorId``). Only its texts reach the
step's error, bounded and with the key removed. A missing or wrong key was answered
403 with an empty body (checked without a key and with an invalid one); the
specification documents 401 for it and 403 for a key without the right.

Outcome of a write: only the statuses the operation's registry entry lists in
``no_effect`` (built from what the operation documents, see tests/reference/plan.py)
mean nothing changed; every other failure after the request was sent leaves the
write ``timeout_unknown``.
"""

from __future__ import annotations

import http.client
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request

from flowsteward_extension_sdk import PinnedPeerError, open_pinned_url

from . import errors
from .connection import Connection
from .errors import ExtensionError

DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024
#: The most read from a JSON answer.
MAX_ANSWER_BYTES = 30 * 1024 * 1024
#: The most read from the PDF answer (a 40 MB file in base64 inside JSON).
MAX_PDF_ANSWER_BYTES = 56 * 1024 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
CHUNK_BYTES = 64 * 1024
#: Shortest window still worth starting a request in.
MIN_CALL_WINDOW_SECONDS = 15.0
_USER_AGENT = "FlowSteward-Agnum/0.1"
_PATH_RE = re.compile(r"^/api(/[A-Za-z0-9%._~-]+){1,5}$")
CHECK = "; check in Agnum what changed before running the step again"


class DeadlineReached(Exception):
    """The invocation's time budget leaves no room for another request."""


@dataclass
class Answer:
    status: int
    raw: bytes
    content_type: str

    @property
    def empty(self) -> bool:
        return not self.raw.strip()


class AgnumTransport:
    """Sends Agnum requests for one connection."""

    def __init__(
        self,
        connection: Connection,
        *,
        opener: Callable[..., Any] = open_pinned_url,
        clock: Callable[[], float] = time.monotonic,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._connection = connection
        self._opener = opener
        self.clock = clock
        self._timeout_seconds = timeout_seconds
        self._call_timeout = timeout_seconds
        #: True once a request that can change data may have left this process.
        self.mutated = False
        #: True once Agnum confirmed a change in this invocation.
        self.committed = False
        #: Monotonic time after which no request may start (set by the operation).
        self.deadline: float | None = None

    @property
    def connection(self) -> Connection:
        return self._connection

    def remaining(self) -> float:
        return float("inf") if self.deadline is None else self.deadline - self.clock()

    # -- public calls --------------------------------------------------------

    def read(
        self,
        path: str,
        query: Sequence[tuple[str, str]] = (),
        *,
        shown: str,
        limit: int | None = None,
        missing_ok: bool = False,
    ) -> Answer:
        """A GET. With ``missing_ok`` a 404 that is Agnum's own "not found" problem is
        returned (status 404, its body) instead of raised: a search that found nothing."""
        return self._call(
            "GET",
            path,
            query,
            None,
            shown=shown,
            mutating=False,
            no_effect=(),
            limit=MAX_ANSWER_BYTES if limit is None else limit,
            missing_ok=missing_ok,
        )

    def write(
        self,
        method: str,
        path: str,
        body: Any,
        *,
        shown: str,
        no_effect: Collection[int],
    ) -> Answer:
        """A POST, PUT, PATCH or DELETE; its answer once Agnum confirmed it."""
        answer = self._call(
            method,
            path,
            (),
            body,
            shown=shown,
            mutating=True,
            no_effect=no_effect,
            limit=MAX_ANSWER_BYTES,
            missing_ok=False,
        )
        self.committed = True
        return answer

    # -- answers -------------------------------------------------------------

    def scrub(self, value: Any) -> Any:
        """The key removed from every text and key of a parsed answer."""
        if isinstance(value, str):
            for form in self._connection.secret_forms():
                if form in value:
                    value = value.replace(form, "[redacted]")
            return value
        if isinstance(value, Mapping):
            return {self.scrub(k): self.scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        return value

    def json(self, answer: Answer) -> Any:
        """The answer's JSON value (numbers with a point as Decimal), key removed.
        ValueError when it is not JSON."""
        return self.scrub(parse_json(answer.raw))

    def text(self, answer: Answer, limit: int = 100_000) -> str:
        """The answer as text, key removed (for answers the specification gives no
        format for)."""
        return self.scrub(answer.raw.decode("utf-8", "replace"))[:limit]

    # -- one request ---------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        query: Sequence[tuple[str, str]],
        body: Any,
        *,
        shown: str,
        mutating: bool,
        no_effect: Collection[int],
        limit: int,
        missing_ok: bool,
    ) -> Answer:
        if not _PATH_RE.fullmatch(path):
            raise errors.invalid("Not an Agnum operation")
        headers = {
            "Accept": "application/json, */*;q=0.5",
            "User-Agent": _USER_AGENT,
            "X-API-KEY": self._connection.api_key,
        }
        data = None
        if body is not None:
            data = dumps_json(body).encode("utf-8")
            if len(data) > MAX_REQUEST_BODY_BYTES:
                raise errors.invalid("The request is larger than the 8 MB this extension sends")
            headers["Content-Type"] = "application/json; charset=utf-8"
        url = self._connection.base_url + path
        if query:
            url += "?" + urlencode(list(query))
        request = Request(url, method=method, data=data, headers=headers)
        remaining = self.remaining()
        if remaining < MIN_CALL_WINDOW_SECONDS:
            raise DeadlineReached
        self._call_timeout = min(self._timeout_seconds, remaining)
        if mutating:
            self.mutated = True
        expires = None if self.deadline is None else self.deadline + MIN_CALL_WINDOW_SECONDS
        args = (request, shown, mutating, tuple(no_effect), limit, missing_ok)
        if expires is None:
            return self._exchange(*args)
        outcome: dict[str, Any] = {}

        def work() -> None:
            try:
                outcome["value"] = self._exchange(*args)
            except BaseException as exc:  # handed to the caller below
                outcome["error"] = exc

        worker = threading.Thread(target=work, name="agnum-call", daemon=True)
        worker.start()
        worker.join(max(0.0, expires - self.clock()))
        if worker.is_alive():
            _late(mutating)
        if "error" in outcome:
            raise outcome["error"]
        return outcome["value"]

    def _exchange(
        self,
        request: Request,
        shown: str,
        mutating: bool,
        no_effect: tuple[int, ...],
        limit: int,
        missing_ok: bool,
    ) -> Answer:
        expires = None if self.deadline is None else self.deadline + MIN_CALL_WINDOW_SECONDS
        try:
            with self._opener(
                request, timeout_seconds=self._call_timeout, purpose="Agnum"
            ) as response:
                status = int(getattr(response, "status", 0) or getattr(response, "code", 0) or 0)
                headers = getattr(response, "headers", None) or {}
                raw = read_bounded(
                    response,
                    limit,
                    mutating=mutating,
                    expires=expires,
                    clock=self.clock,
                    per_read=self._call_timeout,
                )
        except HTTPError as exc:
            try:
                body = exc.read(MAX_ERROR_BODY_BYTES)
            except Exception:  # pragma: no cover - upstream body is optional
                body = b""
            status = int(exc.code)
            if missing_ok and status == 404 and self.problem_status(body) == 404:
                return Answer(404, body, "application/problem+json")
            raise self.refusal(
                status, body, shown, mutating=mutating, no_effect=no_effect
            ) from None
        except PinnedPeerError as exc:
            # Raised while resolving and pinning, before anything was sent.
            host = self._connection.host
            if "cannot be resolved" in str(exc):
                raise ExtensionError(
                    errors.CONNECTION_FAILED,
                    f"{host} could not be resolved; check the Agnum API address",
                    failure_class="transient",
                    retryable=True,
                    definitely_no_external_effect=True,
                    external_effect_status="failed",
                ) from None
            raise errors.no_effect(
                errors.BLOCKED_ADDRESS,
                f"{host} is not reachable under the host network policy (a private or "
                "reserved address is refused)",
            ) from None
        except TimeoutError:
            raise ExtensionError(
                errors.TIMEOUT_UNKNOWN if mutating else errors.TIMEOUT,
                "Agnum did not answer in time" + (CHECK if mutating else ""),
                **_transport_failure_facts(mutating=mutating),
            ) from None
        except (OSError, http.client.HTTPException):
            raise ExtensionError(
                errors.TIMEOUT_UNKNOWN if mutating else errors.CONNECTION_FAILED,
                "The connection to Agnum failed" + (CHECK if mutating else ""),
                **_transport_failure_facts(mutating=mutating),
            ) from None
        if status >= 300 or status < 200:
            raw = raw[:MAX_ERROR_BODY_BYTES]
            if missing_ok and status == 404 and self.problem_status(raw) == 404:
                return Answer(404, raw, "application/problem+json")
            raise self.refusal(status, raw, shown, mutating=mutating, no_effect=no_effect)
        kind = str(headers.get("Content-Type") or "") if hasattr(headers, "get") else ""
        return Answer(status, raw, kind.split(";")[0].strip().lower())

    # -- refusals ------------------------------------------------------------

    def problem_status(self, raw: bytes) -> int | None:
        """The ``status`` of an RFC 7807 problem body, or None when it is not one."""
        try:
            data = parse_json(raw)
        except ValueError:
            return None
        if not isinstance(data, Mapping):
            return None
        status = data.get("status")
        return status if isinstance(status, int) and not isinstance(status, bool) else None

    def detail(self, raw: bytes) -> str:
        """Agnum's own words about a refusal; never the key."""
        try:
            data = self.scrub(parse_json(raw))
        except ValueError:
            text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "replace"))
            return errors.safe_text(self.scrub(text), 200)
        if not isinstance(data, Mapping):
            return errors.safe_text(data, 200)
        parts: list[str] = []
        for key in ("detail", "message", "title"):
            value = data.get(key)
            if isinstance(value, str) and value.strip() and value.strip() not in parts:
                parts.append(value.strip())
                break
        reference = data.get("errorId")
        if isinstance(reference, str) and reference.strip():
            parts.append(f"error id {reference.strip()}")
        return errors.safe_text(" — ".join(parts), 500)

    def refusal(
        self,
        status: int,
        raw: bytes,
        shown: str,
        *,
        mutating: bool,
        no_effect: Collection[int] = (),
    ) -> ExtensionError:
        detail = self.detail(raw)
        suffix = f": {detail}" if detail else ""
        certain = not mutating or status in no_effect
        if status in {401, 403}:
            code = errors.AUTHENTICATION_FAILED
            message = (
                f"Agnum refused {shown} (HTTP {status}){suffix}. The key is missing or wrong, "
                "or it lacks the right this step needs; check the key in the connection"
            )
        elif 300 <= status < 400:
            code = errors.REDIRECT_REJECTED
            message = f"Agnum redirected {shown} (HTTP {status}); redirects are not followed"
        elif status == 404:
            code = errors.NOT_FOUND
            message = f"Agnum found nothing for {shown} (HTTP 404){suffix}"
            if self.problem_status(raw) is None:
                message += "; if this keeps happening, check the Agnum API address"
        elif status == 409:
            code = errors.CONFLICT
            message = f"Agnum refused {shown} as a conflict (HTTP 409){suffix}"
        elif status == 400:
            code = errors.UPSTREAM_VALIDATION_FAILED
            message = f"Agnum refused {shown} (HTTP 400){suffix}"
        elif status >= 500 or status in {408, 429}:
            code = errors.TIMEOUT if status in {408, 504} else errors.UPSTREAM_FAILURE
            message = f"Agnum could not complete {shown} (HTTP {status}){suffix}"
        else:
            code = errors.UPSTREAM_VALIDATION_FAILED
            message = f"Agnum rejected {shown} (HTTP {status}){suffix}"
        if not certain:
            return unknown(message + CHECK, code, status)
        if mutating:
            message += "; nothing was changed"
        return ExtensionError(
            code,
            message,
            failure_class="provider",
            retryable=not mutating and (status >= 500 or status in {408, 429}),
            definitely_no_external_effect=True,
            external_effect_status="failed",
            http_status=status,
        )


def unknown(message: str, code: str, status: int | None) -> ExtensionError:
    return ExtensionError(
        code,
        message,
        failure_class="provider",
        retryable=False,
        definitely_no_external_effect=False,
        external_effect_status="timeout_unknown",
        http_status=status,
    )


def dumps_json(value: Any) -> str:
    """JSON with ``Decimal`` written as an exact number (never through float)."""
    marker = secrets.token_hex(8)

    def default(item: Any) -> str:
        if isinstance(item, Decimal) and item.is_finite():
            return f"@{marker}@{format(item, 'f')}@{marker}@"
        raise TypeError(f"{type(item).__name__} is not JSON")

    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=default)
    return re.sub(rf'"@{marker}@([-+0-9.]+)@{marker}@"', r"\1", text)


def parse_json(raw: bytes) -> Any:
    """Any JSON value, numbers with a point read as Decimal; ValueError otherwise."""
    text = raw.decode("utf-8-sig", "strict") if raw else ""
    if not text.strip():
        raise ValueError("empty")
    try:
        return json.loads(text, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise ValueError("bad JSON") from exc


def read_bounded(
    response: Any,
    limit: int,
    *,
    mutating: bool = False,
    expires: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    per_read: float | None = None,
) -> bytes:
    """Read at most ``limit`` bytes before ``expires``; a longer answer is an error."""
    buffer = bytearray()
    sock = _socket_of(response)
    while True:
        if expires is not None:
            left = expires - clock()
            if left <= 0:
                _late(mutating)
            if sock is not None:
                try:
                    sock.settimeout(max(0.5, min(left, per_read or left)))
                except OSError:  # pragma: no cover - a closed socket fails the read below
                    pass
        chunk = response.read(min(CHUNK_BYTES, limit + 1 - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
        if expires is not None and clock() > expires:
            _late(mutating)
        if len(buffer) > limit:
            if mutating:
                raise unknown(
                    "Agnum's answer was too large to read" + CHECK,
                    errors.RESPONSE_TOO_LARGE,
                    None,
                )
            raise errors.no_effect(
                errors.RESPONSE_TOO_LARGE,
                f"Agnum's answer is larger than the {limit // (1024 * 1024)} MB this extension "
                "reads; narrow the search",
            )
    return bytes(buffer)


def _late(mutating: bool) -> None:
    if not mutating:
        raise DeadlineReached
    raise ExtensionError(
        errors.TIMEOUT_UNKNOWN,
        "Agnum's answer did not arrive in time" + CHECK,
        failure_class="transient",
        retryable=False,
        definitely_no_external_effect=False,
        external_effect_status="timeout_unknown",
    )


def _socket_of(response: Any) -> Any:
    for path in (("fp", "raw", "_sock"), ("fp", "fp", "raw", "_sock"), ("_sock",)):
        node = response
        for name in path:
            node = getattr(node, name, None)
        if node is not None and hasattr(node, "settimeout"):
            return node
    return None


def _transport_failure_facts(*, mutating: bool) -> dict[str, Any]:
    return {
        "failure_class": "transient",
        "retryable": not mutating,
        "definitely_no_external_effect": not mutating,
        "external_effect_status": "timeout_unknown" if mutating else "failed",
    }


__all__ = [
    "CHECK",
    "MAX_PDF_ANSWER_BYTES",
    "MIN_CALL_WINDOW_SECONDS",
    "AgnumTransport",
    "Answer",
    "DeadlineReached",
    "dumps_json",
    "parse_json",
    "read_bounded",
    "unknown",
]
