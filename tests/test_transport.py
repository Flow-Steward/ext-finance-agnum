"""The connection, the wire and what each refusal means."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any

import pytest
from flowsteward_extension_sdk import PinnedPeerError
from support import API_KEY, BASE_URL, FakeAgnum, Seq, dumped, problem, run, text

from runtime.connection import base_url_of
from runtime.errors import ExtensionError
from runtime.transport import dumps_json

ORDER = {
    "doc_no": "A1",
    "date": "2026-01-02",
    "sum": "10.10",
    "vat_sum": "2.12",
    "total": "12.22",
    "client": {"code": "C1"},
    "products": [{"code": "P1", "price": "10.10", "qty": "1", "vat": "2.12", "vat_proc": "21"}],
}


# -- the address --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("https://srv1.elit.lt/agnumAPIv2", "https://srv1.elit.lt/agnumAPIv2"),
        ("https://srv1.elit.lt/agnumAPIv2/", "https://srv1.elit.lt/agnumAPIv2"),
        (" https://Agnum.Example.lt:8443/a/b ", "https://agnum.example.lt:8443/a/b"),
        ("https://agnum.example.lt", "https://agnum.example.lt"),
        ("https://agnum.example.lt:443/x", "https://agnum.example.lt/x"),
    ],
)
def test_an_api_address_is_kept_as_its_root(given: str, expected: str) -> None:
    assert base_url_of(given) == expected


@pytest.mark.parametrize(
    "given",
    [
        "",
        None,
        "http://srv1.elit.lt/agnumAPIv2",
        "https://srv1.elit.lt/agnumAPIv2/swagger-ui/index.html",
        "https://srv1.elit.lt/agnumAPIv2/v3/api-docs",
        "https://srv1.elit.lt/agnumAPIv2/api",
        "https://user:pw@srv1.elit.lt/x",
        "https://srv1.elit.lt/x?a=1",
        "https://srv1.elit.lt/x#f",
        "https://10.0.0.5/agnumAPIv2",
        "https://[::1]/x",
        "https://localhost/x",
        "https://agnum.local/x",
        "https://intranet/x",
        "https://srv1.elit.lt/a b",
        "https://srv1.elit.lt:99999/x",
        "ftp://srv1.elit.lt/x",
    ],
)
def test_an_address_that_is_not_an_https_api_root_is_refused(given: Any) -> None:
    with pytest.raises(ExtensionError) as caught:
        base_url_of(given)
    assert caught.value.code == "invalid_connection"


def test_a_bad_address_fails_before_any_request() -> None:
    fake = FakeAgnum()
    response = run(fake, "clients_get", {"id": 1}, base_url="http://agnum.example.lt/x")
    assert response["error_code"] == "invalid_connection" and fake.calls == []


# -- the wire -------------------------------------------------------------------------------


def test_the_key_travels_only_in_its_header() -> None:
    fake = FakeAgnum({"GET /api/clients/7": {"id": 7}})
    response = run(fake, "clients_get", {"id": 7})
    assert response["ok"] is True
    [call] = fake.calls
    assert call.url == f"{BASE_URL}/api/clients/7"
    assert call.headers["x-api-key"] == API_KEY
    assert API_KEY not in call.url
    assert sum(API_KEY in v for v in call.headers.values()) == 1
    assert call.host == "agnum.example.lt"


def test_amounts_go_on_the_wire_exactly_as_written() -> None:
    fake = FakeAgnum({"POST /api/orders": (201, {"id": 1})})
    big = {**ORDER, "total": "123456789012345.123456", "sum": Decimal("0.1"), "vat_sum": 2}
    response = run(fake, "orders_create", big)
    assert response["ok"] is True, response
    raw = fake.calls[0].raw_body.decode()
    assert '"total":123456789012345.123456' in raw
    assert '"sum":0.1' in raw and '"vat_sum":2' in raw
    assert not re.search(r"\d[eE][+-]?\d", raw)


@pytest.mark.parametrize(
    "amount", ["1e5", "1,5", "NaN", "Infinity", "1234567890123456", "0.1234567"]
)
def test_amounts_that_are_not_plain_decimals_are_refused(amount: str) -> None:
    fake = FakeAgnum()
    response = run(fake, "orders_create", {**ORDER, "total": amount})
    assert response["error_code"] == "invalid_payload" and fake.calls == []


def test_dumps_json_never_goes_through_float() -> None:
    value = {"a": Decimal("0.30000000000000004441"), "b": [Decimal("-1.50")]}
    assert dumps_json(value) == '{"a":0.30000000000000004441,"b":[-1.50]}'


def test_answers_keep_decimals_exact() -> None:
    fake = FakeAgnum({"GET /api/clients/7": (200, b'{"id":7,"debt":0.1000000000000000055511}')})
    response = run(fake, "clients_get", {"id": 7})
    assert response["result"]["data"]["debt"] == "0.1000000000000000055511"


# -- refusals ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_on_a_read_changes_nothing(status: int) -> None:
    fake = FakeAgnum({"GET /api/clients/7": (status, b"")})
    response = run(fake, "clients_get", {"id": 7})
    assert response["error_code"] == "authentication_failed"
    assert response["definitely_no_external_effect"] is True


@pytest.mark.parametrize(
    ("operation_id", "inputs", "key", "status", "certain"),
    [
        (
            "clients_create",
            {"code": "C", "create_date": "2026-01-01"},
            "POST /api/clients",
            400,
            True,
        ),
        (
            "clients_create",
            {"code": "C", "create_date": "2026-01-01"},
            "POST /api/clients",
            403,
            True,
        ),
        (
            "clients_create",
            {"code": "C", "create_date": "2026-01-01"},
            "POST /api/clients",
            500,
            False,
        ),
        (
            "clients_create",
            {"code": "C", "create_date": "2026-01-01"},
            "POST /api/clients",
            404,
            False,
        ),
        ("orders_create", ORDER, "POST /api/orders", 401, True),
        # POST /api/orders documents no 403: its meaning there is not known.
        ("orders_create", ORDER, "POST /api/orders", 403, False),
        # Several lines: a validation refusal may follow a partial save.
        ("orders_create", ORDER, "POST /api/orders", 400, False),
        ("orders_create", ORDER, "POST /api/orders", 409, False),
        ("products_update", {"id": 5, "name": "X"}, "PUT /api/products", 404, True),
        ("products_update", {"id": 5, "name": "X"}, "PUT /api/products", 409, False),
        ("production_norms_delete", {"id": 5}, "DELETE /api/production/norms/5", 409, True),
        ("production_norms_delete", {"id": 5}, "DELETE /api/production/norms/5", 404, False),
        ("production_norms_delete", {"id": 5}, "DELETE /api/production/norms/5", 400, False),
        ("production_norms_update", {"id": 5}, "PUT /api/production/norms", 400, False),
        ("production_norms_update", {"id": 5}, "PUT /api/production/norms", 403, True),
        ("production_norms_patch", {"id": 5}, "PATCH /api/production/norms", 404, False),
    ],
)
def test_what_a_refused_write_means(
    operation_id: str, inputs: dict[str, Any], key: str, status: int, certain: bool
) -> None:
    fake = FakeAgnum({key: problem(status, "refused")})
    response = run(fake, operation_id, inputs)
    assert response["ok"] is False
    assert len(fake.calls) == 1  # never retried
    assert response["definitely_no_external_effect"] is certain, response
    assert response["external_effect_status"] == ("failed" if certain else "timeout_unknown")
    assert ("nothing was changed" in response["error"]) is certain


def test_the_problem_detail_reaches_the_error_without_the_key() -> None:
    body = problem(400, f"validation failed: Price cannot be null (key {API_KEY})")
    fake = FakeAgnum({"POST /api/clients": body})
    response = run(fake, "clients_create", {"code": "C", "create_date": "2026-01-01"})
    assert "Price cannot be null" in response["error"]
    assert API_KEY not in dumped(response)


def test_a_server_error_names_its_tracking_id() -> None:
    body = (
        500,
        {
            "title": "Internal Server Error",
            "status": 500,
            "detail": "unknown error #a1b2",
            "errorId": "a1b2-c3",
        },
    )
    fake = FakeAgnum({"GET /api/clients/7": body})
    response = run(fake, "clients_get", {"id": 7})
    assert response["error_code"] == "upstream_failure"
    assert "a1b2-c3" in response["error"]


def test_a_redirect_is_not_followed() -> None:
    fake = FakeAgnum({"GET /api/clients/7": (302, b"")})
    response = run(fake, "clients_get", {"id": 7})
    assert response["error_code"] == "redirect_rejected"


def test_a_404_that_is_not_agnums_problem_points_at_the_address() -> None:
    fake = FakeAgnum({"GET /api/clients/7": text("<html>Not Found</html>", 404)})
    response = run(fake, "clients_get", {"id": 7})
    assert response["error_code"] == "not_found"
    assert "address" in response["error"]


def test_an_unresolvable_host_is_a_connection_failure() -> None:
    fake = FakeAgnum({"GET /api/clients/7": PinnedPeerError("host cannot be resolved")})
    response = run(fake, "clients_get", {"id": 7})
    assert response["error_code"] == "connection_failed"


def test_a_private_address_is_blocked() -> None:
    fake = FakeAgnum({"GET /api/clients/7": PinnedPeerError("address is private")})
    response = run(fake, "clients_get", {"id": 7})
    assert response["error_code"] == "blocked_address"
    assert response["definitely_no_external_effect"] is True


def test_a_write_that_times_out_is_unknown_and_not_retried() -> None:
    fake = FakeAgnum({"POST /api/clients": TimeoutError()})
    response = run(fake, "clients_create", {"code": "C", "create_date": "2026-01-01"})
    assert response["error_code"] == "timeout_unknown"
    assert response["definitely_no_external_effect"] is False
    assert len(fake.calls) == 1


def test_a_dropped_connection_on_a_read_is_safe_to_retry() -> None:
    fake = FakeAgnum({"GET /api/clients/7": ConnectionResetError()})
    response = run(fake, "clients_get", {"id": 7})
    assert response["error_code"] == "connection_failed"
    assert response["retryable"] is True


def test_no_request_starts_without_time_to_finish() -> None:
    times = Seq([0.0] + [10_000.0] * 10)
    fake = FakeAgnum({"GET /api/clients/7": {"id": 7}})
    response = run(fake, "clients_get", {"id": 7}, clock=lambda: times.pop(0) if times else 1e9)
    assert response["error_code"] == "timeout"
    assert fake.calls == []


def test_an_answer_too_large_is_refused() -> None:
    from runtime import transport

    fake = FakeAgnum({"GET /api/clients/7": (200, b"[" + b"1," * 1_600_000 + b"1]")})
    old = transport.MAX_ANSWER_BYTES
    transport.MAX_ANSWER_BYTES = 2 * 1024 * 1024
    try:
        response = run(fake, "clients_get", {"id": 7})
    finally:
        transport.MAX_ANSWER_BYTES = old
    assert response["error_code"] == "response_too_large"
    assert "larger than the 2 MB this extension reads" in response["error"]


def test_the_key_is_never_in_a_result() -> None:
    fake = FakeAgnum(
        {"GET /api/clients/7": {"id": 7, "memo": f"echo {API_KEY}", API_KEY: json.dumps(API_KEY)}}
    )
    response = run(fake, "clients_get", {"id": 7})
    assert response["ok"] is True
    assert API_KEY not in dumped(response)
