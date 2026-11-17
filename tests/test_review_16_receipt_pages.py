"""Regressions of the 2026-11-17 review: what may prove that Agnum has no record, and
what a write's result keeps when it is too large.

Numbers are the review's: 3 (an empty answer is no proof), 9 (large write results),
14 (strong supplier keys before e-mail), 16 (every page of the receipt check).
"""

from __future__ import annotations

from typing import Any

import pytest
from support import FakeAgnum, Seq, run

from runtime.transport import AgnumTransport

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


# -- 16: the receipt check reads every page ---------------------------------------------------


def _page(first_id: int, count: int, supplier: str = "S2") -> list[dict[str, Any]]:
    return [
        {"id": first_id + i, "doc_no": "INV-9", "client": {"code": supplier}} for i in range(count)
    ]


def test_16_a_receipt_of_the_same_supplier_on_the_second_page_is_found() -> None:
    first = _page(1, 250)
    second = [{"id": 900, "doc_no": "INV-9", "client": {"code": "S1"}}]
    fake = FakeAgnum({RECEIPTS: Seq([first, second]), "POST /api/receipts": (201, {"id": 12})})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["error_code"] == "conflict" and "900" in response["error"], response
    assert fake.keys() == [RECEIPTS, RECEIPTS]
    assert dict(fake.calls[1].query)["offset"] == "250"


def test_16_every_page_is_read_before_a_receipt_is_created() -> None:
    fake = FakeAgnum(
        {RECEIPTS: Seq([_page(1, 250), _page(251, 3)]), "POST /api/receipts": (201, {"id": 12})}
    )
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["ok"] is True, response
    assert fake.keys() == [RECEIPTS, RECEIPTS, "POST /api/receipts"]
    first, second = dict(fake.calls[0].query), dict(fake.calls[1].query)
    assert (first["limit"], first["offset"], first["order"]) == ("250", "0", "id")
    assert (second["limit"], second["offset"]) == ("250", "250")


def test_16_a_failure_between_pages_creates_nothing() -> None:
    fake = FakeAgnum(
        {
            RECEIPTS: Seq([_page(1, 250), ConnectionResetError("reset")]),
            "POST /api/receipts": (201, {"id": 12}),
        }
    )
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["ok"] is False
    assert response["definitely_no_external_effect"] is True
    assert fake.keys() == [RECEIPTS, RECEIPTS]


def test_16_running_out_of_time_between_pages_creates_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    left = [500.0]

    def remaining(self: AgnumTransport) -> float:
        return left.pop(0) if left else 1.0

    monkeypatch.setattr(AgnumTransport, "remaining", remaining)
    fake = FakeAgnum(
        {RECEIPTS: Seq([_page(1, 250), _page(251, 3)]), "POST /api/receipts": (201, {"id": 12})}
    )
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["ok"] is False
    assert response["definitely_no_external_effect"] is True
    assert fake.keys() == [RECEIPTS]


def test_16_a_page_agnum_repeats_is_not_trusted() -> None:
    page = _page(1, 250)
    fake = FakeAgnum({RECEIPTS: Seq([page, page]), "POST /api/receipts": (201, {"id": 12})})
    response = run(fake, "create_purchase_receipt_if_new", RECEIPT)
    assert response["error_code"] == "invalid_response", response
    assert "POST /api/receipts" not in fake.keys()
