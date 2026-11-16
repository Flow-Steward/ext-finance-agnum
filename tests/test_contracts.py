"""The generated contracts match their sources; forms, inputs and the runtime agree."""

from __future__ import annotations

import collections
import hashlib
import json
import re
import struct
import sys
from typing import Any

import pytest
import yaml
from support import BUNDLE

sys.path.insert(0, str(BUNDLE / "tests" / "reference"))

import build_contracts  # noqa: E402
import plan  # noqa: E402

from runtime import catalog, composites, generic  # noqa: E402
from runtime.connection import CONFIG_FIELDS, SECRET_FIELDS  # noqa: E402

OUTPUTS = build_contracts.outputs()
SHA = "1d5c67dbfc3983624ec1932989d8adfdebb572f02c76e8497067b78aa4bbc527"


def load(path: str) -> Any:
    return yaml.safe_load((BUNDLE / path).read_text(encoding="utf-8"))


def operations() -> list[dict[str, Any]]:
    return load("contracts/operation_manifest.yaml")["operations"]


@pytest.mark.parametrize("path", sorted(p for p in OUTPUTS if "/" in p))
def test_generated_files_are_current(path: str) -> None:
    assert (BUNDLE / path).read_text(encoding="utf-8") == build_contracts.render(
        path, OUTPUTS[path]
    ), f"{path} drifted: run python3 tests/reference/build_contracts.py"


def test_every_documented_operation_is_a_step() -> None:
    facts = plan.facts()
    assert facts["source"]["sha256"] == SHA and facts["source"]["api_version"] == "1.47.2"
    documented = {(o["method"], o["path"]) for o in facts["operations"]}
    assert len(documented) == 39
    assert collections.Counter(m for m, _ in documented) == {
        "GET": 22,
        "POST": 8,
        "PUT": 5,
        "PATCH": 2,
        "DELETE": 2,
    }
    registry = generic.registry()
    assert len(registry) == 39
    assert {(e["method"], e["path"]) for e in registry.values()} == documented
    assert set(plan.TITLES) == documented
    assert set(plan.DESCRIPTIONS) == set(registry)


def test_step_ids_come_from_the_method_and_the_path_not_the_vendor_ids() -> None:
    vendor = {o["vendor_operation_id"] for o in plan.operations()}
    for oid, entry in generic.registry().items():
        assert oid == plan.operation_id(entry["method"], entry["path"])
        assert oid not in vendor
    assert plan.operation_id("GET", "/api/clients/{id}/doc/search") == "clients_id_doc_search"
    assert plan.operation_id("DELETE", "/api/production/norms/{id}") == "production_norms_delete"
    assert plan.operation_id("PUT", "/api/products/price") == "products_price_update"


def test_every_step_is_registered_once_and_routable() -> None:
    ids = [o["operation_id"] for o in operations()]
    assert len(ids) == len(set(ids)) == len(catalog.OPERATION_IDS) == 45
    assert set(ids) == set(catalog.OPERATION_IDS)
    assert not set(composites.COMPOSITES) & set(generic.registry())
    titles = [o["display_name"] for o in operations()]
    assert len(titles) == len(set(titles))
    for title in titles:
        assert "Advanced" not in title and "·" not in title
        assert title.split()[0][0].isupper()


def test_effects_are_the_writes() -> None:
    manifest = load("extension.yaml")
    assert manifest["external_effects"] == OUTPUTS["external_effects"]
    effects = {e["operation_id"] for e in manifest["external_effects"]}
    assert effects == set(catalog.EFFECT_OPERATION_IDS)
    assert len(effects) == 20  # 17 generated writes + 3 everyday
    for op in operations():
        assert (op["operation_kind"] == "action") == (op["operation_id"] in effects)


def test_every_form_field_is_an_input_and_the_runtime_takes_it() -> None:
    forms = {f["operation_id"]: f for f in load("contracts/step_ui_manifest.yaml")["forms"]}
    for op in operations():
        oid = op["operation_id"]
        inputs = {i["name"] for i in op["inputs"]} - {"connection_ref"}
        assert {f["name"] for f in forms[oid]["fields"]} == inputs, oid
        if oid in composites.INPUTS:
            assert set(composites.INPUTS[oid]) == inputs, oid
        elif oid in generic.registry():
            assert generic._inputs_of(generic.spec(oid)) == inputs, oid


def test_the_create_if_new_steps_take_their_generated_steps_inputs() -> None:
    ops = {o["operation_id"]: o for o in operations()}
    for everyday, generated in (
        ("create_sales_document_if_new", "orders_create"),
        ("create_purchase_receipt_if_new", "receipts_create"),
    ):
        assert ops[everyday]["inputs"] == ops[generated]["inputs"]
    mine = {i["name"]: i for i in ops["find_or_create_client"]["inputs"]}
    theirs = {i["name"]: i for i in ops["clients_create"]["inputs"]}
    assert set(mine) == set(theirs)
    assert theirs["create_date"]["required"] is True and mine["create_date"]["required"] is False


def test_every_step_declares_warnings() -> None:
    for op in operations():
        assert "warnings" in [o["name"] for o in op["outputs"]], op["operation_id"]


def test_the_connection_form_matches_the_connection_type() -> None:
    [kind] = load("contracts/connection_types.yaml")["connection_types"]
    assert set(kind["config_schema"]["properties"]) == set(CONFIG_FIELDS)
    assert set(kind["secret_schema"]["properties"]) == set(SECRET_FIELDS) == {"api_key"}
    form = load("ui/pages/connection.yaml")["components"][0]
    assert {f["path"] for f in form["fields"]} == set(CONFIG_FIELDS) | set(SECRET_FIELDS)
    assert form["data"]["test_action"] == catalog.TEST_CONNECTION
    assert load("extension.yaml")["egress"] == {"connection_fields": ["agnum.base_url"]}


def test_coverage_names_every_operation() -> None:
    cov = json.loads((BUNDLE / "tests/reference/coverage.json").read_text())
    assert cov["source"]["sha256"] == SHA
    assert len(cov["endpoints"]) == 39
    for row in cov["endpoints"]:
        assert row["status"] == "step" and row["release"] == "0.1.0", row
    registry = json.loads((BUNDLE / "runtime/registry.json").read_text())
    assert registry["source"] == cov["source"] == plan.facts()["source"]


def test_the_icon_is_a_square_png_described_in_branding() -> None:
    data = (BUNDLE / "icon.png").read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and width == height and 32 <= width <= 512
    assert hashlib.sha256(data).hexdigest() in (BUNDLE / "BRANDING.md").read_text()


def test_ui_pages_hold_no_markdown_tables() -> None:
    for path in (BUNDLE / "ui" / "pages").glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*\|.*\|\s*$", text, re.M), path
        assert "|+" not in text, path


def test_customer_texts_list_nothing_unverified() -> None:
    texts = [BUNDLE / "README.md", BUNDLE / "CHANGELOG.md", BUNDLE / "extension.yaml"]
    texts += sorted((BUNDLE / "ui").rglob("*.yaml"))
    for path in texts:
        text = path.read_text(encoding="utf-8")
        for phrase in ("Not verified", "not verified", "Limits", "Advanced ·"):
            assert phrase not in text, (path.name, phrase)


def test_the_runtime_names_no_server() -> None:
    """The address is the connection's: the runtime holds no host of its own."""
    for path in (BUNDLE / "runtime").glob("*.py"):
        hosts = re.findall(r"https?://([a-z0-9.-]+)", path.read_text(encoding="utf-8"))
        assert set(hosts) <= {"server.example.lt"}, (path, hosts)


def test_versions_agree() -> None:
    version = load("extension.yaml")["version"]
    assert re.search(rf"^## {re.escape(version)} — ", (BUNDLE / "CHANGELOG.md").read_text(), re.M)


def test_health() -> None:
    import health

    report = health.check_health()
    assert report["ok"] is True and report["declared_operations"] == 45
    assert report["specification_sha256"] == SHA


def test_the_picker_lists_everyday_steps_first_and_dangerous_ones_last_in_each_group() -> None:
    ops = operations()
    assert [o["operation_id"] for o in ops[:6]] == list(composites.COMPOSITES)
    assert ops[6]["operation_id"] == "orders_pdf_get"
    rows = {r["operation_id"]: r for r in plan.plan()}
    order = [g for g, _ in build_contracts.GROUPS]
    keys = [
        (
            order.index(rows[o["operation_id"]]["group"]),
            build_contracts.DANGER_ORDER[rows[o["operation_id"]]["danger"]],
        )
        for o in ops[6:]
    ]
    assert keys == sorted(keys)


def test_dangerous_steps_are_strict_and_say_what_cannot_be_undone() -> None:
    registry = generic.registry()
    ops = {o["operation_id"]: o for o in operations()}
    deletes = [r for r in plan.plan() if r["danger"] == "delete"]
    irreversible = [r for r in plan.plan() if r["danger"] == "irreversible"]
    assert {r["operation_id"] for r in deletes} == {
        "production_norms_delete",
        "processing_norms_delete",
    }
    assert {r["operation_id"] for r in irreversible} == {
        "production_norms_update",
        "processing_norms_update",
        "production_norms_patch",
        "processing_norms_patch",
    }
    for row in deletes:
        assert ops[row["operation_id"]]["description"].count("Deletion cannot be undone.") == 1
        assert registry[row["operation_id"]]["strict"] is True
        assert registry[row["operation_id"]]["no_effect"] == [401, 403, 409]
    for row in irreversible:
        entry = registry[row["operation_id"]]
        assert "the step cannot undo it" in ops[row["operation_id"]]["description"]
        assert entry["strict"] is True
        assert entry["no_effect"] == [401, 403]  # not even 400
        assert next(n for n in entry["body"]["children"] if n["name"] == "id")["required"]


def test_the_outcome_table_follows_the_documented_statuses() -> None:
    """A refused key changes nothing anywhere; 400, 404 and 409 only where documented."""
    for row in plan.plan():
        if row["kind"] not in plan.WRITE_KINDS:
            assert row["no_effect"] == []
            continue
        assert {401, 403} <= set(row["no_effect"]), row["operation_id"]
        documented_only = set(row["no_effect"]) - {401, 403}
        assert set(map(str, documented_only)) <= set(row["codes"]), row["operation_id"]
        if 400 in row["no_effect"]:
            assert row["objects"] == "one" and not row["danger"]
    table = {r["operation_id"]: r["no_effect"] for r in plan.plan()}
    # A refused key changes nothing even where 403 is not documented (POST /api/orders,
    # /api/customer-returns): the security filter answers before routing.
    assert table["orders_create"] == table["customer_returns_create"] == [401, 403]
    assert table["products_update"] == [400, 401, 403, 404]
    assert table["products_price_update"] == [401, 403]


def test_objects_one_only_when_the_request_holds_no_list_of_records() -> None:
    counts = collections.Counter(r["objects"] for r in plan.plan() if r["objects"])
    assert counts == {"one": 7, "several": 10}
    several = {r["operation_id"] for r in plan.plan() if r["objects"] == "several"}
    assert {
        "orders_create",
        "receipts_create",
        "customer_returns_create",
        "products_price_update",
    } <= several


def test_write_descriptions_name_the_rights_and_repeats() -> None:
    ops = {o["operation_id"]: o for o in operations()}
    for row in plan.plan():
        description = ops[row["operation_id"]]["description"]
        for right in row["rights"]:
            assert right in description, row["operation_id"]
        if row["kind"] == "create":
            assert "creates the record again" in description
    assert "ignores the others" in ops["products_price_update"]["description"]
