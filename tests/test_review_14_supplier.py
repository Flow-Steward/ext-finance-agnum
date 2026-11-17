"""Review 2026-11-17, item 14: id, code and company code tell suppliers apart; e-mail
alone never decides."""

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


# -- 14: id, code and company code decide; e-mail alone cannot ---------------------------------


def _receipt_with(client: dict[str, Any]) -> dict[str, Any]:
    return {"id": 4, "doc_no": "INV-9", "client": client}


def test_14_other_ids_with_the_same_email_are_another_supplier() -> None:
    other = _receipt_with({"id": 6, "code": "S2", "company_code": "399", "email": "acc@x.lt"})
    fake = FakeAgnum({RECEIPTS: [other], "POST /api/receipts": (201, {"id": 12})})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["ok"] is True, response
    assert "another supplier" in response["result"]["warnings"][0]


def test_14_the_same_id_with_another_email_is_the_same_supplier() -> None:
    same = _receipt_with({"id": 5, "code": "S1", "company_code": "300000001", "email": "new@x.lt"})
    fake = FakeAgnum({RECEIPTS: [same]})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["error_code"] == "conflict" and "same supplier" in response["error"]


def test_14_only_the_email_cannot_compare_the_supplier() -> None:
    only_email = _receipt_with({"email": "acc@x.lt"})
    fake = FakeAgnum({RECEIPTS: [only_email]})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["error_code"] == "conflict" and "cannot be compared" in response["error"]
    assert fake.keys() == [RECEIPTS]


def test_14_contradicting_strong_keys_stop_the_step() -> None:
    mixed = _receipt_with({"id": 5, "code": "S9", "email": "acc@x.lt"})
    fake = FakeAgnum({RECEIPTS: [mixed]})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["error_code"] == "conflict" and "contradict" in response["error"]
    assert fake.keys() == [RECEIPTS]
