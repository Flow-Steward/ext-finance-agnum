"""The pinned facts are what the pinned specification says."""

from __future__ import annotations

import json
import sys

import pytest
from support import BUNDLE

sys.path.insert(0, str(BUNDLE / "tests" / "reference"))

import extract_reference  # noqa: E402
import nodes  # noqa: E402
import plan  # noqa: E402

SOURCE = extract_reference.SOURCE


@pytest.mark.skipif(not SOURCE.exists(), reason="the vendor document is not committed")
def test_the_facts_are_extracted_from_the_pinned_document() -> None:
    raw = SOURCE.read_bytes()
    assert extract_reference.extract(raw) == json.loads(
        extract_reference.OUTPUT.read_text(encoding="utf-8")
    )


def test_the_pin_is_the_documented_one() -> None:
    assert plan.facts()["source"]["sha256"] == extract_reference.SHA256
    assert plan.facts()["source"]["bytes"] == 89054


def test_a_reference_with_a_space_and_a_point_resolves() -> None:
    ref = "#/components/schemas/customer return doc."
    assert extract_reference.ref_name(ref) == "customer return doc."
    assert extract_reference.ref_name("#/components/schemas/a%20b~1c") == "a b/c"
    op = next(
        o
        for o in plan.operations()
        if o["path"] == "/api/customer-returns" and o["method"] == "POST"
    )
    assert op["body"]["schema"]["ref"] == "customer return doc."
    assert "return_no" in plan.schema("customer return doc.")["properties"]


def test_rights_are_read_from_every_operation() -> None:
    for op in plan.operations():
        assert op["rights"], op["path"]
        assert all(r.isupper() for r in op["rights"])
        assert "Required rights" not in op["description"]


def test_combined_limit_offset_is_never_an_input() -> None:
    """ClientOrdersSearch, ProductSearch… document one "limit/offset" key: not sent."""
    combined = []
    for op in plan.operations():
        spec = nodes.search_spec(op)
        if spec and spec["combined_paging"]:
            combined.append(op["path"])
            assert not spec["paging"]
            assert all("/" not in f["name"] for f in spec["fields"])
    assert sorted(combined) == [
        "/api/clients/doc/search",
        "/api/clients/{id}/doc/search",
        "/api/products/price/search",
        "/api/products/qty/search",
        "/api/products/search",
    ]


def test_filters_are_the_schema_fields_and_nothing_else() -> None:
    for op in plan.operations():
        spec = nodes.search_spec(op)
        if not spec or spec["schema"] is None:
            continue
        props = set(plan.schema(spec["schema"])["properties"])
        names = {f["name"] for f in spec["fields"]}
        assert names == props - nodes.SEARCH_CONTROLS, op["path"]
        assert set(spec["filter_type"]) == {"eq", "ne", "co", "gt", "lt", "le", "ge"}
