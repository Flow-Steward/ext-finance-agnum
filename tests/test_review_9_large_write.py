"""Review 2026-11-17, item 9: a write's result too large for the step output keeps its
identifiers and the write's facts, also through the real main.py."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from typing import Any

import pytest
from support import BUNDLE, FakeAgnum, not_found, run

from runtime import budget

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


# -- 9: a large write result keeps its ids and the write's facts ------------------------------


def _big_client() -> dict[str, Any]:
    return {"id": 77, "code": "C1", "name": "UAB X", "notes": ["x" * 400] * 20_000}


def test_9_a_large_created_client_keeps_its_id_and_fits() -> None:
    fake = FakeAgnum({"POST /api/clients": (201, _big_client())})
    response = run(fake, "clients_create", {"code": "C1", "create_date": "2026-01-01"})
    assert response["ok"] is True, response.get("error")
    assert response["external_effect_status"] == "succeeded"
    assert response["definitely_no_external_effect"] is False
    result = response["result"]
    assert result["id"] == 77
    assert result["data"]["id"] == 77 and result["data"]["code"] == "C1"
    assert "notes" not in result["data"]
    assert any("too large" in w for w in result["warnings"])
    assert budget.fits(result)


def test_9_a_large_client_of_find_or_create_keeps_its_id() -> None:
    fake = FakeAgnum(
        {CLIENTS: not_found("/api/clients/search"), "POST /api/clients": (201, _big_client())}
    )
    response = run(fake, "find_or_create_client", {"code": "C1", "create_date": "2026-01-01"})
    assert response["ok"] is True, response.get("error")
    assert response["result"]["client_id"] == 77
    assert response["result"]["created"] is True
    assert budget.fits(response["result"])


def _main_with(script: str) -> tuple[int, dict[str, Any], int]:
    """Run the real main.py, its dispatch replaced by ``script``'s ``dispatch``."""
    code = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, "tests")
        import main
        {script}
        main.dispatch_runtime = dispatch
        raise SystemExit(main.main())
        """
    ).format(script=textwrap.indent(textwrap.dedent(script), "").strip())
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BUNDLE,
        input=b"{}",
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert proc.stderr == b"", proc.stderr.decode()[-2000:]
    return proc.returncode, json.loads(proc.stdout), len(proc.stdout)


def test_9_main_runs_a_large_write_and_keeps_the_write_facts() -> None:
    code, response, _ = _main_with(
        """
        from support import FakeAgnum, run
        def dispatch(payload):
            big = {"id": 77, "code": "C1", "notes": ["x" * 400] * 20000}
            fake = FakeAgnum({"POST /api/clients": (201, big)})
            return run(fake, "clients_create", {"code": "C1", "create_date": "2026-01-01"})
        """
    )
    assert code == 0, response
    assert response["external_effect_status"] == "succeeded"
    assert response["result"]["id"] == 77


def _padded(size: int) -> str:
    """A dispatch whose serialized response (plus the newline) is exactly ``size`` bytes."""
    return f"""
        import json
        def response(pad):
            return {{"ok": True, "result": {{"id": 77, "pad": "x" * pad}}, "error_code": None,
                     "error": None, "errors": [], "external_effect_status": "succeeded",
                     "definitely_no_external_effect": False}}
        base = len(main._json_bytes(response(0))) + 1
        def dispatch(payload):
            return response({size} - base)
        """


def test_9_main_passes_a_response_exactly_at_the_limit() -> None:
    from main import MAX_SERIALIZED_RESPONSE_BYTES

    code, response, size = _main_with(_padded(MAX_SERIALIZED_RESPONSE_BYTES))
    assert size == MAX_SERIALIZED_RESPONSE_BYTES
    assert code == 0 and response["result"]["id"] == 77


def test_9_main_one_byte_over_the_limit_keeps_the_write_facts() -> None:
    from main import MAX_SERIALIZED_RESPONSE_BYTES

    code, response, _ = _main_with(_padded(MAX_SERIALIZED_RESPONSE_BYTES + 1))
    assert code == 2
    assert response["error_code"] == "response_too_large"
    assert response["external_effect_status"] == "succeeded"
    assert response["definitely_no_external_effect"] is False
    assert response["result"] == {"id": 77}
