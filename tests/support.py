"""A fake Agnum DB API, an in-memory artifact plane and helpers."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from decimal import Decimal
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit

from runtime.operations import handle_runtime
from runtime.transport import AgnumTransport, parse_json

BUNDLE = Path(__file__).resolve().parents[1]
API_KEY = "agnum-fixture-ApiKey-0123456789abcdef"  # pragma: allowlist secret
BASE_URL = "https://agnum.example.lt/agnumAPIv2"
PREFIX = "/agnumAPIv2"


class Call:
    def __init__(self, request: Any) -> None:
        parts = urlsplit(request.full_url)
        self.url = request.full_url
        self.host = parts.hostname
        self.path = parts.path[len(PREFIX) :] if parts.path.startswith(PREFIX) else parts.path
        self.query = parse_qsl(parts.query, keep_blank_values=True)
        self.verb = request.get_method()
        self.headers = {k.lower(): v for k, v in request.header_items()}
        self.raw_body = request.data or b""
        self.body: Any = parse_json(self.raw_body) if self.raw_body else None

    @property
    def key(self) -> str:
        return f"{self.verb} {self.path}"


class Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = Message()
        for key, value in (headers or {"Content-Type": "application/json"}).items():
            self.headers[key] = value

    def read(self, size: int = -1) -> bytes:
        chunk, self._body = (
            (self._body, b"") if size < 0 else (self._body[:size], self._body[size:])
        )
        return chunk

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def problem(status: int, detail: str = "", instance: str = "/api/orders") -> tuple:
    """An RFC 7807 refusal as the specification documents it."""
    titles = {
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        500: "Internal Server Error",
    }
    return (
        status,
        {
            "title": titles.get(status, "Error"),
            "status": status,
            "detail": detail,
            "instance": instance,
            "timestamp": "2026-01-15T14:30:00.000+00:00",
            "message": detail,
        },
    )


def not_found(instance: str = "/api/products/search") -> tuple:
    """The specification's NotFound example: what a search that finds nothing answers."""
    return problem(404, "product ABC123 not found", instance)


def text(body: str, status: int = 200) -> tuple:
    return (status, body.encode(), {"Content-Type": "text/plain"})


def _encode(value: Any) -> bytes:
    def default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return float(item)
        raise TypeError

    return json.dumps(value, default=default).encode()


class Seq(list):
    """Answers used one after the other (a plain list is one JSON list answer)."""


class FakeAgnum:
    """Answers by "METHOD /api/path".

    An answer is a JSON value (HTTP 200), a (status, body[, headers]) tuple, an
    exception to raise, a callable taking the Call, or a Seq of those used in order.
    """

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[Call] = []

    def __call__(self, request: Any, *, timeout_seconds: float, purpose: str) -> Response:
        call = Call(request)
        self.calls.append(call)
        if call.key not in self.routes:
            raise AssertionError(f"unexpected call to {call.key}")
        route = self.routes[call.key]
        answer = route.pop(0) if isinstance(route, Seq) else route
        if callable(answer) and not isinstance(answer, BaseException):
            answer = answer(call)
        if isinstance(answer, BaseException):
            raise answer
        headers = None
        if isinstance(answer, tuple):
            status, body = answer[0], answer[1]
            headers = answer[2] if len(answer) > 2 else None
        else:
            status, body = 200, answer
        raw = body if isinstance(body, bytes) else _encode(body)
        if status >= 300:
            raise HTTPError(request.full_url, status, "error", Message(), io.BytesIO(raw))
        return Response(status, raw, headers)

    def keys(self) -> list[str]:
        return [c.key for c in self.calls]


class Files:
    """An in-memory artifact plane."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.written: list[dict[str, Any]] = []

    def reader(self, payload: Any, *, artifact_id: str, timeout_seconds: float) -> bytes:
        raise AssertionError("no step reads a workflow file")

    def writer(self, payload: Any, chunks: list[bytes], **kw: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("storage down")
        self.written.append({"data": b"".join(chunks), **kw})
        return {"artifact_handle": f"art_{len(self.written)}"}


def payload(
    operation_id: str,
    inputs: dict[str, Any] | None = None,
    *,
    test_mode: bool = False,
    api_key: str = API_KEY,
    base_url: str = BASE_URL,
) -> dict[str, Any]:
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "runtime_context": {
            "group_id": None,
            "for_each_id": None,
            "step_id": "s",
            "step_execution_id": "5c1d2e3f-4a5b-4c6d-8e7f-000000000001",
            "test_mode": test_mode,
        },
        "action": {
            "action_id": operation_id,
            "input": {"connection_ref": "c1", **(inputs or {})},
            "target": {
                "connection": {
                    "connection_id": "c1",
                    "connection_type_id": "agnum",
                    "config": {"label": "UAB X", "base_url": base_url},
                    "secrets": {"api_key": api_key},
                }
            },
        },
    }


def run(
    fake: FakeAgnum,
    operation_id: str,
    inputs: dict[str, Any] | None = None,
    *,
    clock: Callable[[], float] | None = None,
    files: Files | None = None,
    **kw: Any,
) -> dict[str, Any]:
    files = files or Files()

    def factory(connection: Any) -> AgnumTransport:
        if clock is None:
            return AgnumTransport(connection, opener=fake)
        return AgnumTransport(connection, opener=fake, clock=clock)

    return handle_runtime(
        payload(operation_id, inputs, **kw),
        transport_factory=factory,
        artifact_reader=files.reader,
        artifact_writer=files.writer,
    )


def dumped(response: Any) -> str:
    return json.dumps(response, default=str, ensure_ascii=False)
