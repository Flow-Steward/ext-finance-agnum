"""The everyday steps: strict searches, no near-duplicates, documents once per number."""

from __future__ import annotations

from typing import Any

import pytest
from support import FakeAgnum, Seq, not_found, problem, run, text

CLIENTS = "GET /api/clients/search"
PRODUCTS = "GET /api/products/search"
ORDERS = "GET /api/orders/search"
RECEIPTS = "GET /api/receipts/search"
ORDER = {
    "doc_no": "SF-1",
    "date": "2026-01-02",
    "sum": "10.00",
    "vat_sum": "2.10",
    "total": "12.10",
    "client": {"code": "C1"},
    "products": [{"code": "P1", "price": "10", "qty": "1", "vat": "2.10", "vat_proc": "21"}],
}
RECEIPT = {
    "doc_no": "INV-9",
    "date": "2026-01-02",
    "receipt_date": "2026-01-03",
    "vat": "2.10",
    "client": {"code": "S1", "company_code": "300000001"},
    "products": [{"code": "P1", "price": "10", "qty": "1", "vat": "2.10", "vat_proc": "21"}],
}


# -- test connection ------------------------------------------------------------------------


@pytest.mark.parametrize("answer", [[], not_found("/api/clients/search")])
def test_the_connection_test_passes_on_a_search_that_finds_nothing(answer: Any) -> None:
    fake = FakeAgnum({CLIENTS: answer})
    response = run(fake, "test_connection")
    assert response["result"] == {"connected": True, "warnings": []}
    assert fake.calls[0].query == [("code", "FLOWSTEWARD-CONNECTION-TEST"), ("filter_type", "eq")]


@pytest.mark.parametrize(
    ("answer", "code"),
    [
        ((403, b""), "authentication_failed"),
        (problem(401), "authentication_failed"),
        (text("<html>Welcome</html>"), "invalid_response"),
        (text("<html>nginx</html>", 404), "not_found"),
    ],
)
def test_the_connection_test_explains_a_failure(answer: Any, code: str) -> None:
    response = run(FakeAgnum({CLIENTS: answer}), "test_connection")
    assert response["error_code"] == code


# -- find client ----------------------------------------------------------------------------


def test_find_client_compares_every_value_again() -> None:
    client = {"id": 5, "code": "c1", "company_code": "123", "email": "A@B.LT"}
    fake = FakeAgnum({CLIENTS: [client]})
    response = run(fake, "find_client", {"code": "C1", "company_code": "123", "email": "a@b.lt"})
    assert response["result"]["found"] is True and response["result"]["client_id"] == 5
    assert fake.calls[0].query == [
        ("code", "C1"),
        ("client_code", "123"),
        ("email", "a@b.lt"),
        ("filter_type", "eq"),
    ]


def test_find_client_stops_when_agnum_did_not_filter() -> None:
    fake = FakeAgnum({CLIENTS: [{"id": 5, "code": "C1"}, {"id": 6, "code": "C2"}]})
    response = run(fake, "find_client", {"code": "C1"})
    assert response["error_code"] == "invalid_response"
    assert "cannot be trusted" in response["error"]


def test_find_client_with_two_matches_is_a_conflict() -> None:
    fake = FakeAgnum({CLIENTS: [{"id": 5, "code": "C1"}, {"id": 6, "code": "c1"}]})
    response = run(fake, "find_client", {"code": "C1"})
    assert response["error_code"] == "conflict"


def test_find_client_finding_nothing_is_not_an_error() -> None:
    response = run(
        FakeAgnum({CLIENTS: not_found("/api/clients/search")}), "find_client", {"email": "x@y.lt"}
    )
    assert response["result"] == {"found": False, "client_id": None, "client": None, "warnings": []}


def test_find_client_needs_a_value() -> None:
    assert run(FakeAgnum(), "find_client", {})["error_code"] == "invalid_payload"
    assert run(FakeAgnum(), "find_client", {"name": "X"})["error_code"] == "invalid_payload"


# -- find or create client ------------------------------------------------------------------


def test_an_existing_client_is_returned_not_created() -> None:
    fake = FakeAgnum({CLIENTS: [{"id": 5, "code": "C1"}]})
    response = run(fake, "find_or_create_client", {"code": "C1", "name": "UAB"})
    assert response["result"]["created"] is False and response["result"]["client_id"] == 5
    assert response["external_effect_status"] == "succeeded"
    assert [c.verb for c in fake.calls] == ["GET"]


def test_a_new_client_is_created_with_todays_date_by_default() -> None:
    fake = FakeAgnum(
        {
            CLIENTS: Seq([not_found(), [], []]),
            "POST /api/clients": (201, {"id": 77, "code": "C1"}),
        }
    )
    response = run(
        fake,
        "find_or_create_client",
        {
            "code": "C1",
            "company_code": "300",
            "name": "UAB",
            "client_role": ["BUYER"],
        },
    )
    assert response["result"]["created"] is True and response["result"]["client_id"] == 77
    body = fake.calls[-1].body
    assert body["code"] == "C1" and body["company_code"] == "300"
    assert body["client_role"] == ["BUYER"] and len(body["create_date"]) == 10


def test_a_near_duplicate_client_is_not_created() -> None:
    fake = FakeAgnum({CLIENTS: Seq([[], [], [{"id": 9, "company_code": "300", "code": "OTHER"}]])})
    response = run(
        fake, "find_or_create_client", {"code": "C1", "company_code": "300", "name": "UAB"}
    )
    assert response["error_code"] == "conflict"
    assert "client 9" in response["error"]
    assert all(c.verb == "GET" for c in fake.calls)


def test_a_client_search_that_cannot_be_trusted_creates_nothing() -> None:
    fake = FakeAgnum({CLIENTS: [{"id": 9, "code": "SOMEONE-ELSE"}]})
    response = run(fake, "find_or_create_client", {"code": "C1", "name": "UAB"})
    assert response["error_code"] == "invalid_response"
    assert all(c.verb == "GET" for c in fake.calls)


def test_bad_client_inputs_stop_before_any_request() -> None:
    fake = FakeAgnum()
    response = run(fake, "find_or_create_client", {"code": "C1", "client_role": ["KING"]})
    assert response["error_code"] == "invalid_payload" and fake.calls == []


# -- find product ---------------------------------------------------------------------------


def test_find_product_matches_one_of_its_barcodes() -> None:
    fake = FakeAgnum({PRODUCTS: [{"id": 3, "code": "P1", "barcode": ["111", "4770000000001"]}]})
    response = run(fake, "find_product", {"barcode": "4770000000001", "wh": "S001"})
    assert response["result"]["product_id"] == 3
    assert sorted(fake.calls[0].query) == [
        ("barcode", "4770000000001"),
        ("filter_type", "eq"),
        ("wh", "S001"),
    ]


def test_find_product_stops_on_a_product_with_another_barcode() -> None:
    fake = FakeAgnum({PRODUCTS: [{"id": 3, "code": "P1", "barcode": ["111"]}]})
    assert run(fake, "find_product", {"barcode": "222"})["error_code"] == "invalid_response"


# -- documents once per number ---------------------------------------------------------------


@pytest.mark.parametrize("nothing", [[], not_found("/api/orders/search")])
def test_a_new_sales_document_is_created(nothing: Any) -> None:
    fake = FakeAgnum({ORDERS: nothing, "POST /api/orders": (201, {"id": "88", "order_no": "SF-1"})})
    response = run(fake, "create_sales_document_if_new", ORDER)
    assert response["ok"] is True, response
    assert response["result"]["id"] == "88"
    assert fake.calls[0].query == [("order_no", "SF-1"), ("filter_type", "eq")]
    assert fake.keys() == [ORDERS, "POST /api/orders"]


def test_a_sales_document_number_agnum_has_is_not_created_again() -> None:
    fake = FakeAgnum({ORDERS: [{"id": 7, "doc_no": "sf-1"}]})
    response = run(fake, "create_sales_document_if_new", ORDER)
    assert response["error_code"] == "conflict"
    assert response["definitely_no_external_effect"] is True
    assert fake.keys() == [ORDERS]


def test_a_sales_document_search_that_returns_other_numbers_stops() -> None:
    fake = FakeAgnum({ORDERS: [{"id": 7, "doc_no": "SF-100"}]})
    response = run(fake, "create_sales_document_if_new", ORDER)
    assert response["error_code"] == "invalid_response"
    assert fake.keys() == [ORDERS]


def test_a_failed_duplicate_search_writes_nothing() -> None:
    fake = FakeAgnum({ORDERS: problem(500, "boom")})
    response = run(fake, "create_sales_document_if_new", ORDER)
    assert response["error_code"] == "upstream_failure"
    assert response["definitely_no_external_effect"] is True
    assert fake.keys() == [ORDERS]


def test_a_receipt_from_the_same_supplier_is_not_created_again() -> None:
    existing = {"id": 4, "doc_no": "INV-9", "client": {"code": "S1", "company_code": "300000001"}}
    fake = FakeAgnum({RECEIPTS: [existing]})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["error_code"] == "conflict" and "same supplier" in response["error"]


def test_a_receipt_whose_supplier_cannot_be_compared_stops() -> None:
    fake = FakeAgnum({RECEIPTS: [{"id": 4, "doc_no": "INV-9"}]})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["error_code"] == "conflict" and "cannot be compared" in response["error"]


def test_the_same_number_from_another_supplier_is_a_new_receipt() -> None:
    other = {"id": 4, "doc_no": "INV-9", "client": {"code": "S2", "company_code": "399"}}
    fake = FakeAgnum({RECEIPTS: [other], "POST /api/receipts": (201, {"id": 12})})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["ok"] is True, response
    assert "another supplier" in response["result"]["warnings"][0]
    assert fake.calls[0].query == [
        ("receipt_no", "INV-9"),
        ("filter_type", "eq"),
        ("order", "id"),
        ("limit", "250"),
        ("offset", "0"),
    ]


def test_a_document_refused_after_the_check_is_reported_as_the_write_says() -> None:
    fake = FakeAgnum({ORDERS: [], "POST /api/orders": problem(400, "Price cannot be null")})
    response = run(fake, "create_sales_document_if_new", ORDER)
    # Several lines: a 400 may follow a partial save.
    assert response["external_effect_status"] == "timeout_unknown"
    assert "Price cannot be null" in response["error"]


# -- what counts as "nothing found" (R2) ------------------------------------------------------

SPRING_404 = (
    404,
    {
        "timestamp": "2026-11-16T10:00:00.000+00:00",
        "status": 404,
        "error": "Not Found",
        "path": "/agnumAPIv2/api/clients/search",
    },
)
SPEC_404 = (  # the specification's NotFound example, word for word
    404,
    {
        "title": "Not Found",
        "status": 404,
        "detail": "product ABC123 not found",
        "instance": "/api/products/search",
        "timestamp": "2026-01-15T14:30:00.000+00:00",
        "message": "product ABC123 not found",
    },
)
EMPTY_404 = (404, b"")
UNTITLED_404 = (404, {"status": 404, "detail": "", "title": ""})


@pytest.mark.parametrize("answer", [SPRING_404, EMPTY_404, UNTITLED_404])
def test_a_404_that_is_not_agnums_problem_fails_the_connection_test(answer: Any) -> None:
    response = run(FakeAgnum({CLIENTS: answer}), "test_connection")
    assert response["error_code"] == "not_found"
    assert "address" in response["error"]


def test_the_specifications_not_found_example_is_nothing_found() -> None:
    assert run(FakeAgnum({CLIENTS: SPEC_404}), "test_connection")["ok"] is True
    response = run(FakeAgnum({CLIENTS: SPEC_404}), "find_client", {"code": "C1"})
    assert response["result"]["found"] is False


@pytest.mark.parametrize("answer", [SPRING_404, EMPTY_404, UNTITLED_404])
def test_a_404_that_is_not_agnums_problem_is_not_nothing_found(answer: Any) -> None:
    response = run(FakeAgnum({CLIENTS: answer}), "find_client", {"code": "C1"})
    assert response["error_code"] == "not_found"


@pytest.mark.parametrize("answer", [SPRING_404, EMPTY_404, UNTITLED_404])
@pytest.mark.parametrize(
    ("operation_id", "search", "inputs"),
    [
        ("find_or_create_client", CLIENTS, {"code": "C1", "name": "UAB"}),
        ("create_sales_document_if_new", ORDERS, ORDER),
        ("create_purchase_receipt_if_new", RECEIPTS, RECEIPT),
    ],
)
def test_a_lookup_404_that_is_not_agnums_problem_writes_nothing(
    answer: Any, operation_id: str, search: str, inputs: dict[str, Any]
) -> None:
    fake = FakeAgnum({search: answer})
    response = run(fake, operation_id, inputs)
    assert response["error_code"] == "not_found"
    assert response["definitely_no_external_effect"] is True
    assert all(c.verb == "GET" for c in fake.calls)


# -- an invented value is said (M1) ----------------------------------------------------------


def test_a_default_creation_date_is_named_in_the_warnings() -> None:
    fake = FakeAgnum({CLIENTS: not_found(), "POST /api/clients": (201, {"id": 1})})
    response = run(fake, "find_or_create_client", {"code": "C1", "name": "UAB"})
    assert "create_date was not given; today (UTC) was sent" in response["result"]["warnings"]
    fake = FakeAgnum({CLIENTS: not_found(), "POST /api/clients": (201, {"id": 1})})
    response = run(
        fake, "find_or_create_client", {"code": "C1", "name": "UAB", "create_date": "2020-05-05"}
    )
    assert response["result"]["warnings"] == []
    assert fake.calls[-1].body["create_date"] == "2020-05-05"
