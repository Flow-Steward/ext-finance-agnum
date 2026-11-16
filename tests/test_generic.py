"""The generated steps: requests built from the specification, answers read as documented."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from support import FakeAgnum, Files, dumped, not_found, problem, run, text

from runtime import catalog, generic
from runtime.operations import neutral_result

PDF = b"%PDF-1.4\n%\xe2\xe3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


# -- requests ------------------------------------------------------------------------------


def test_a_search_sends_its_filters_as_separate_query_parameters() -> None:
    fake = FakeAgnum({"GET /api/orders/search": [{"id": 1, "doc_no": "A1"}]})
    response = run(
        fake,
        "orders_search",
        {
            "order_no": "A 1&x=y",
            "date": "2026-01-31",
            "modify": "2026-01-31 10:00:00",
            "filter_type": "co",
            "order": "date",
            "limit": 50,
            "offset": 100,
        },
    )
    assert response["ok"] is True, response
    assert fake.calls[0].query == [
        ("order_no", "A 1&x=y"),
        ("date", "2026-01-31"),
        ("modify", "2026-01-31 10:00:00"),
        ("filter_type", "co"),
        ("order", "date"),
        ("limit", "50"),
        ("offset", "100"),
    ]
    assert "A+1%26x%3Dy" in fake.calls[0].url
    assert response["result"]["items"] == [{"id": 1, "doc_no": "A1"}]
    assert response["result"]["count"] == 1


@pytest.mark.parametrize(
    "inputs",
    [
        {"order_no": "A1", "unknown": 1},
        {"order_no": "A1", "filter_type": "like"},
        {"order_no": "A1", "order": "total"},
        {"order_no": "A1", "limit": 251},
        {"order_no": "A1", "limit": 0},
        {"order_no": "A1", "offset": -1},
        {"date": "31.01.2026"},
        {"modify": "2026-01-31T10:00:00"},
        {"modify": "2026-01-31 25:00:00"},
        {"id": "abc"},
        {"order_no": "x" * 201},
        {},  # at least one filter
        {"filter_type": "eq"},
    ],
)
def test_a_search_refuses_what_its_schema_does_not_allow(inputs: dict[str, Any]) -> None:
    fake = FakeAgnum()
    response = run(fake, "orders_search", inputs)
    assert response["error_code"] == "invalid_payload", inputs
    assert fake.calls == []


def test_a_combined_limit_offset_is_never_sent() -> None:
    fake = FakeAgnum({"GET /api/products/search": []})
    assert run(fake, "products_search", {"limit/offset": "10"})["error_code"] == "invalid_payload"
    assert run(fake, "products_search", {"limit": 10})["error_code"] == "invalid_payload"
    response = run(fake, "products_search", {"code": "P1"})
    assert response["ok"] is True
    assert [k for k, _ in fake.calls[0].query] == ["code"]


def test_a_search_that_finds_nothing_is_an_empty_list() -> None:
    fake = FakeAgnum({"GET /api/products/search": not_found()})
    response = run(fake, "products_search", {"code": "NONE"})
    assert response["result"] == {"items": [], "count": 0, "warnings": []}


def test_a_search_404_that_is_not_agnums_problem_is_an_error() -> None:
    fake = FakeAgnum({"GET /api/products/search": text("<html>nginx</html>", 404)})
    response = run(fake, "products_search", {"code": "NONE"})
    assert response["error_code"] == "not_found"


def test_the_client_documents_search_takes_the_client_in_the_path() -> None:
    fake = FakeAgnum({"GET /api/clients/12/doc/search": []})
    response = run(
        fake,
        "clients_id_doc_search",
        {"client_id": 12, "id": "5", "paid": "n", "min_date": "2026-01-01"},
    )
    assert response["ok"] is True, response
    assert fake.calls[0].query == [("id", "5"), ("paid", "n"), ("min-date", "2026-01-01")]
    assert (
        run(fake, "clients_id_doc_search", {"client_id": 12, "paid": "maybe"})["error_code"]
        == "invalid_payload"
    )


def test_the_processing_norm_search_takes_a_field_list() -> None:
    fake = FakeAgnum({"GET /api/processing/norms/search": []})
    response = run(fake, "processing_norms_search", {"norm_code": "P", "fields": ["id", "memo"]})
    assert response["ok"] is True, response
    assert ("fields", "id,memo") in fake.calls[0].query
    bad = run(fake, "processing_norms_search", {"norm_code": "P", "fields": ["password"]})
    assert bad["error_code"] == "invalid_payload"


def test_the_tracking_search_takes_the_tracking_fields_only() -> None:
    fake = FakeAgnum({"GET /api/orders/tracking/search": [{"id": 1, "track_no": "T"}]})
    assert run(fake, "orders_tracking_search", {"doc_no": "A1"})["ok"] is True
    assert run(fake, "orders_tracking_search", {"anything": "x"})["error_code"] == "invalid_payload"
    assert run(fake, "orders_tracking_search", {})["error_code"] == "invalid_payload"


def test_a_document_number_is_quoted_into_the_path() -> None:
    fake = FakeAgnum({"GET /api/orders/A%2F1%3F": {"doc_no": "A/1?"}})
    response = run(fake, "orders_get", {"docNo": "A/1?"})
    assert response["ok"] is True, response


@pytest.mark.parametrize("ident", [0, -1, "1.5", True, 10**16, "x"])
def test_an_id_must_be_a_positive_whole_number(ident: Any) -> None:
    fake = FakeAgnum()
    response = run(fake, "production_norms_delete", {"id": ident})
    assert response["error_code"] == "invalid_payload" and fake.calls == []


# -- bodies -------------------------------------------------------------------------------


def test_a_body_is_closed_and_typed() -> None:
    fake = FakeAgnum()
    for inputs in (
        {"code": "C", "create_date": "2026-01-01", "password": "x"},
        {"code": "", "create_date": "2026-01-01", "name": "x" * 251},
        {"code": "C", "create_date": "2026-13-01"},
        {"code": "C", "create_date": "2026-01-01", "client_role": ["BOSS"]},
        {"code": "C", "create_date": "2026-01-01", "client_role": ["BUYER", "BUYER"]},
        {"code": "C", "create_date": "2026-01-01", "pozym_numbers": [101]},
        {"code": "C", "create_date": "2026-01-01", "adr": {"city": "V", "planet": "Mars"}},
        {"code": "C", "create_date": "2026-01-01", "price_group": "four"},
        {"code": "C"},  # create_date is required by the specification
    ):
        response = run(fake, "clients_create", inputs)
        assert response["error_code"] == "invalid_payload", inputs
    assert fake.calls == []


def test_read_only_fields_are_not_sent_on_create() -> None:
    ops = {i for i in generic._inputs_of(generic.spec("products_create"))}
    assert "id" not in ops and "balance" not in ops
    assert "id" in generic._inputs_of(generic.spec("products_update"))
    assert "id" not in generic._inputs_of(generic.spec("production_norms_create"))


def test_a_price_list_needs_a_product_per_row_and_warns_about_price_levels() -> None:
    fake = FakeAgnum({"PUT /api/products/price": True})
    bad = run(fake, "products_price_update", {"prices": [{"kn1": "1.50"}]})
    assert bad["error_code"] == "invalid_payload"
    assert (
        run(fake, "products_price_update", {"prices": [{"product_code": "P", "sv_min": 1}]})[
            "error_code"
        ]
        == "invalid_payload"
    )
    response = run(
        fake,
        "products_price_update",
        {"prices": [{"product_code": "P", "kn1": "1.50", "wh_all": True}]},
    )
    assert response["ok"] is True, response
    assert fake.calls[0].body == [{"product_code": "P", "wh_all": True, "kn1": 1.5}]
    assert any("price levels" in w for w in response["result"]["warnings"])


def test_a_receipt_line_needs_a_product_id_or_code() -> None:
    receipt = {
        "doc_no": "R1",
        "date": "2026-01-01",
        "receipt_date": "2026-01-01",
        "vat": "0",
        "client": {"code": "S1"},
        "products": [{"price": "1", "qty": "1", "vat": "0", "vat_proc": "0"}],
    }
    assert run(FakeAgnum(), "receipts_create", receipt)["error_code"] == "invalid_payload"


def test_a_client_update_names_the_client() -> None:
    assert (
        run(FakeAgnum(), "clients_update", {"name": "X", "create_date": "2026-01-01"})["error_code"]
        == "invalid_payload"
    )


def test_patch_rows_delete_by_rec_id_only() -> None:
    fake = FakeAgnum({"PATCH /api/production/norms": {"id": 5}})
    for rows in (
        [{"delete": True}],
        [{"rec_id": 4, "delete": True, "product_id": 9}],
        [{"rec_id": 0, "delete": True}],
    ):
        response = run(fake, "production_norms_patch", {"id": 5, "raw_products": rows})
        assert response["error_code"] == "invalid_payload", rows
    assert fake.calls == []
    response = run(
        fake,
        "production_norms_patch",
        {
            "id": 5,
            "raw_products": [
                {"rec_id": 456, "delete": True},
                {"rec_id": 789, "product_id": 10, "formula_brutto": "updated"},
                {"product_id": 15, "formula_brutto": "new row"},
            ],
        },
    )
    assert response["ok"] is True, response
    assert fake.calls[0].body["raw_products"][0] == {"rec_id": 456, "delete": True}


def test_rec_id_and_delete_are_patch_only() -> None:
    fake = FakeAgnum()
    response = run(
        fake, "production_norms_update", {"id": 5, "raw_products": [{"rec_id": 4, "delete": True}]}
    )
    assert response["error_code"] == "invalid_payload"


def test_a_norm_change_needs_its_id() -> None:
    for operation_id in (
        "production_norms_update",
        "production_norms_patch",
        "processing_norms_update",
        "processing_norms_patch",
    ):
        assert run(FakeAgnum(), operation_id, {"memo": "x"})["error_code"] == "invalid_payload"


def test_the_timesheet_takes_the_documented_example_fields() -> None:
    fake = FakeAgnum(
        {"POST /api/employee/timesheet": (201, b"saved", {"Content-Type": "text/plain"})}
    )
    inputs = {"date": "2024-12-31", "driverID": "11", "workTimeDay": 19800, "workTimeNight": 12000}
    response = run(fake, "employee_timesheet_create", inputs)
    assert response["ok"] is True, response
    assert fake.calls[0].body == inputs
    assert response["result"]["text"] == "saved" and response["result"]["data"] is None
    assert (
        run(fake, "employee_timesheet_create", {**inputs, "workTimeDay": -1})["error_code"]
        == "invalid_payload"
    )


# -- answers --------------------------------------------------------------------------------


def test_a_string_answer_is_json_when_it_is_json_and_text_otherwise() -> None:
    fake = FakeAgnum({"GET /api/orders/tracking/A1": "TRACK-1"})
    response = run(fake, "orders_tracking_get", {"docNo": "A1"})
    assert response["result"]["data"] == "TRACK-1" and response["result"]["text"] is None
    fake = FakeAgnum({"GET /api/orders/tracking/A1": text("TRACK-1")})
    response = run(fake, "orders_tracking_get", {"docNo": "A1"})
    assert response["result"]["data"] is None and response["result"]["text"] == "TRACK-1"


def test_stock_quantities_documented_as_a_string_are_read_as_given() -> None:
    fake = FakeAgnum({"GET /api/products/qty/search": [{"code": "P", "qty": 1.5}]})
    response = run(fake, "products_qty_search", {"code": "P"})
    assert response["result"]["items"] == [{"code": "P", "qty": "1.5"}]
    fake = FakeAgnum({"GET /api/products/qty/search": text("P;1.5")})
    response = run(fake, "products_qty_search", {"code": "P"})
    assert response["result"]["items"] == [] and response["result"]["text"] == "P;1.5"


def test_a_price_search_documented_as_one_record_is_a_list_of_one() -> None:
    fake = FakeAgnum({"GET /api/products/price/search": {"product_code": "P", "kn1": 2}})
    response = run(fake, "products_price_search", {"code": "P"})
    assert response["result"]["items"] == [{"product_code": "P", "kn1": 2}]
    assert response["result"]["warnings"]


def test_a_customer_return_documented_without_content() -> None:
    fake = FakeAgnum({"GET /api/customer-returns/G1": (200, b"")})
    response = run(fake, "customer_returns_get", {"docNo": "G1"})
    assert response["result"] == {
        "data": None,
        "text": None,
        "warnings": ["Agnum answered without content"],
    }


def test_a_record_answer_that_is_not_json_is_refused() -> None:
    fake = FakeAgnum({"GET /api/clients/7": text("<html/>")})
    assert run(fake, "clients_get", {"id": 7})["error_code"] == "invalid_response"


@pytest.mark.parametrize("answer", [False, None, "ok"])
def test_a_boolean_write_that_does_not_say_true_is_unknown(answer: Any) -> None:
    fake = FakeAgnum({"PUT /api/clients": answer if answer is not None else (200, b"")})
    response = run(fake, "clients_update", {"code": "C", "create_date": "2026-01-01"})
    assert response["error_code"] == "invalid_response", response
    assert response["external_effect_status"] == "timeout_unknown"


def test_a_delete_reports_the_id_it_sent() -> None:
    fake = FakeAgnum({"DELETE /api/processing/norms/9": True})
    response = run(fake, "processing_norms_delete", {"id": "9"})
    assert response["result"]["deleted_id"] == 9 and response["result"]["data"] is True
    assert fake.calls[0].raw_body == b""


def test_a_created_document_returns_its_id_and_numbers_as_given() -> None:
    fake = FakeAgnum(
        {"POST /api/customer-returns": (201, {"id": 42, "return_no": "G1", "reg_no": "R"})}
    )
    response = run(
        fake,
        "customer_returns_create",
        {
            "doc_date": "2026-01-01",
            "return_date": "2026-01-02",
            "return_no": "G1",
            "sum": "1",
            "vat_sum": "0",
            "total": "1",
            "client": {"code": "C"},
        },
    )
    assert response["ok"] is True, response
    assert response["result"]["id"] == 42
    assert response["result"]["data"] == {"id": 42, "return_no": "G1", "reg_no": "R"}


# -- the PDF ---------------------------------------------------------------------------------


def _pdf_answer(content: bytes = PDF, **extra: Any) -> dict[str, Any]:
    return {
        "id": 123456,
        "file_name": "ABC123456.pdf",
        "doc_no": "ABC123456",
        "file": base64.b64encode(content).decode(),
        **extra,
    }


def test_the_pdf_is_saved_as_a_workflow_file_and_never_returned() -> None:
    files = Files()
    fake = FakeAgnum({"GET /api/orders/pdf/ABC123456": _pdf_answer()})
    response = run(fake, "orders_pdf_get", {"docNo": "ABC123456"}, files=files)
    assert response["ok"] is True, response
    assert files.written[0]["data"] == PDF
    assert files.written[0]["content_type"] == "application/pdf"
    result = response["result"]
    assert result == {
        "file": "art_1",
        "file_name": "ABC123456.pdf",
        "doc_no": "ABC123456",
        "document_id": 123456,
        "size_bytes": len(PDF),
        "warnings": [],
    }
    assert base64.b64encode(PDF).decode()[:20] not in dumped(response)


@pytest.mark.parametrize(
    "answer",
    [
        {"id": 1, "file_name": "x.pdf"},
        {"file": "not base64 !!"},
        {"file": base64.b64encode(b"<html>").decode()},
        [1, 2],
    ],
)
def test_a_pdf_answer_without_a_pdf_is_refused(answer: Any) -> None:
    files = Files()
    fake = FakeAgnum({"GET /api/orders/pdf/A1": answer})
    response = run(fake, "orders_pdf_get", {"docNo": "A1"}, files=files)
    assert response["error_code"] == "invalid_response"
    assert files.written == []


def test_a_pdf_that_cannot_be_saved_is_an_error() -> None:
    fake = FakeAgnum({"GET /api/orders/pdf/A1": _pdf_answer()})
    response = run(fake, "orders_pdf_get", {"docNo": "A1"}, files=Files(fail=True))
    assert response["error_code"] == "artifact_output_unavailable"


def test_a_pdf_file_name_is_made_safe() -> None:
    fake = FakeAgnum({"GET /api/orders/pdf/A1": _pdf_answer(file_name="../../etc/pass\nwd.pdf")})
    response = run(fake, "orders_pdf_get", {"docNo": "A1"})
    assert (
        "/" not in response["result"]["file_name"] and "\n" not in response["result"]["file_name"]
    )


# -- test mode --------------------------------------------------------------------------------


SAMPLES: dict[str, dict[str, Any]] = {
    "products_create": {"code": "P1", "name": "Product"},
    "products_update": {"id": 1, "name": "Product"},
    "products_price_update": {"prices": [{"product_code": "P1", "kn1": "1"}]},
    "production_norms_create": {"product_id": 1, "norm_code": "N"},
    "production_norms_update": {"id": 1},
    "production_norms_patch": {"id": 1},
    "production_norms_delete": {"id": 1},
    "processing_norms_create": {"norm_code": "N"},
    "processing_norms_update": {"id": 1},
    "processing_norms_patch": {"id": 1},
    "processing_norms_delete": {"id": 1},
    "clients_create": {"code": "C", "create_date": "2026-01-01"},
    "clients_update": {"code": "C", "create_date": "2026-01-01"},
    "receipts_create": {
        "doc_no": "R1",
        "date": "2026-01-01",
        "receipt_date": "2026-01-01",
        "vat": "0",
        "client": {"code": "S"},
    },
    "orders_create": {
        "doc_no": "A1",
        "date": "2026-01-01",
        "sum": "1",
        "vat_sum": "0",
        "total": "1",
        "client": {"code": "C"},
    },
    "employee_timesheet_create": {
        "date": "2024-12-31",
        "driverID": "11",
        "workTimeDay": 1,
        "workTimeNight": 0,
    },
    "customer_returns_create": {
        "doc_date": "2026-01-01",
        "return_date": "2026-01-01",
        "return_no": "G1",
        "sum": "1",
        "vat_sum": "0",
        "total": "1",
        "client": {"code": "C"},
    },
    "find_or_create_client": {"code": "C", "name": "UAB"},
    "create_sales_document_if_new": {
        "doc_no": "A1",
        "date": "2026-01-01",
        "sum": "1",
        "vat_sum": "0",
        "total": "1",
        "client": {"code": "C"},
    },
    "create_purchase_receipt_if_new": {
        "doc_no": "R1",
        "date": "2026-01-01",
        "receipt_date": "2026-01-01",
        "vat": "0",
        "client": {"code": "S"},
    },
}


def test_every_write_has_a_sample() -> None:
    assert set(SAMPLES) == set(catalog.EFFECT_OPERATION_IDS)


@pytest.mark.parametrize("operation_id", sorted(SAMPLES))
def test_a_test_run_checks_the_inputs_and_sends_nothing(operation_id: str) -> None:
    fake = FakeAgnum()
    response = run(fake, operation_id, SAMPLES[operation_id], test_mode=True)
    assert response["ok"] is True, response
    assert response["definitely_no_external_effect"] is True
    assert fake.calls == []
    neutral = neutral_result(operation_id, SAMPLES[operation_id])
    assert set(response["result"]) == set(neutral)


@pytest.mark.parametrize("operation_id", sorted(SAMPLES))
def test_a_test_run_still_refuses_bad_inputs(operation_id: str) -> None:
    response = run(FakeAgnum(), operation_id, {**SAMPLES[operation_id], "bogus": 1}, test_mode=True)
    assert response["error_code"] == "invalid_payload"


@pytest.mark.parametrize("operation_id", sorted(SAMPLES))
def test_a_real_write_sends_exactly_one_change(operation_id: str) -> None:
    def answer(call: Any) -> Any:
        if call.verb == "GET":
            return not_found(call.path)
        if call.path in {"/api/clients"} and call.verb == "PUT":
            return True
        if call.path == "/api/products/price" or call.verb == "DELETE":
            return True
        return (201, {"id": 1})

    routes: dict[str, Any] = {}
    for oid, entry in generic.registry().items():
        routes[f"{entry['method']} {entry['path'].replace('{id}', '1')}"] = answer
    fake = FakeAgnum(routes)
    response = run(fake, operation_id, SAMPLES[operation_id])
    assert response["ok"] is True, response
    writes = [c for c in fake.calls if c.verb != "GET"]
    assert len(writes) == 1
    assert response["external_effect_status"] == "succeeded"


def test_problem_helper_is_rfc7807() -> None:
    status, body = problem(400, "x")
    assert status == 400 and body["status"] == 400 and body["detail"] == "x"
