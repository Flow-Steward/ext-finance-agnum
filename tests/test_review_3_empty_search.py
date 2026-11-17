"""Regressions of the 2026-11-17 review: what may prove that Agnum has no record, and
what a write's result keeps when it is too large.

Numbers are the review's: 3 (an empty answer is no proof), 9 (large write results),
14 (strong supplier keys before e-mail), 16 (every page of the receipt check).
"""

from __future__ import annotations

from typing import Any

import pytest
from support import FakeAgnum, run


CLIENTS = "GET /api/clients/search"
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
    "client": {"id": 5, "code": "S1", "company_code": "300000001", "email": "acc@x.lt"},
    "products": [{"code": "P1", "price": "10", "qty": "1", "vat": "2.10", "vat_proc": "21"}],
}
EMPTY_ANSWERS = [
    pytest.param((200, b""), id="empty-body"),
    pytest.param((200, b"   \n"), id="whitespace"),
    pytest.param((200, b"null"), id="json-null"),
]


# -- 3: an empty or null answer does not prove that nothing is there -------------------------


@pytest.mark.parametrize("answer", EMPTY_ANSWERS)
def test_3_an_empty_client_search_creates_no_client(answer: Any) -> None:
    fake = FakeAgnum({CLIENTS: answer, "POST /api/clients": (201, {"id": 1})})
    response = run(fake, "find_or_create_client", {"code": "C1", "create_date": "2026-01-01"})
    assert response["error_code"] == "invalid_response", response
    assert response["definitely_no_external_effect"] is True
    assert fake.keys() == [CLIENTS]


@pytest.mark.parametrize("answer", EMPTY_ANSWERS)
def test_3_an_empty_order_search_creates_no_sales_document(answer: Any) -> None:
    fake = FakeAgnum({ORDERS: answer, "POST /api/orders": (201, {"id": 1})})
    response = run(fake, "create_sales_document_if_new", ORDER)
    assert response["error_code"] == "invalid_response", response
    assert fake.keys() == [ORDERS]


@pytest.mark.parametrize("answer", EMPTY_ANSWERS)
def test_3_an_empty_receipt_search_creates_no_receipt(answer: Any) -> None:
    fake = FakeAgnum({RECEIPTS: answer, "POST /api/receipts": (201, {"id": 1})})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["error_code"] == "invalid_response", response
    assert fake.keys() == [RECEIPTS]


@pytest.mark.parametrize("answer", EMPTY_ANSWERS)
def test_3_an_empty_answer_to_find_client_is_not_nothing_found(answer: Any) -> None:
    response = run(FakeAgnum({CLIENTS: answer}), "find_client", {"code": "C1"})
    assert response["error_code"] == "invalid_response", response


@pytest.mark.parametrize("answer", EMPTY_ANSWERS)
def test_3_a_plain_search_step_still_shows_an_empty_answer_with_a_warning(answer: Any) -> None:
    response = run(FakeAgnum({CLIENTS: answer}), "clients_search", {"code": "C1"})
    assert response["ok"] is True, response
    assert response["result"]["items"] == []
    assert response["result"]["warnings"] == ["Agnum answered without content"]
