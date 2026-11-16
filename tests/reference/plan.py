"""What each documented Agnum operation becomes: its step id, title, group, kind and outcome rules.

Inputs: tests/reference/agnum_api.json (extract_reference.py). Every one of the 39
operations is a step. Step ids are built from the method and the path, never from
the vendor's operationId (create_1, getById_1, …): those are generated and change,
and a step id may not change after a release.

``kind``
    get      a read of one record (by id or document number)
    search   a filtered read of records (query parameters)
    pdf      a read whose answer carries a PDF in base64, saved as a workflow file
    create   POST — a new record
    update   PUT — changes a record
    patch    PATCH — changes a record and adds, changes or deletes its rows
    delete   DELETE — deletes a record

``danger``
    ""            an ordinary step
    irreversible  a write that deletes data as part of what it does (a norm replaced,
                  rows deleted by a patch): explicit ID, strict outcome
    delete        a deletion: explicit ID, strict outcome

``objects`` (writes): "one" when the request names one record whose fields may hold
scalar lists or nested objects; "several" when it carries a list of records or rows
(document lines, norm rows, a price list) — a refusal can then follow a partial save.

``no_effect``: the HTTP statuses after which a write certainly changed nothing: 401 and
403 on every write (the key is refused before routing, see AUTH_REFUSALS), and 400,
404 and 409 only where the operation documents them and they can prove it. Every other
status after a write was sent leaves it ``timeout_unknown``.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FACTS = HERE / "agnum_api.json"
RELEASE = "0.1.0"

#: (method, path) → (title, group override or None). The group is the vendor's tag unless
#: overridden; the PDF read is an everyday step.
TITLES: dict[tuple[str, str], tuple[str, str | None]] = {
    ("POST", "/api/products"): ("Create product", None),
    ("PUT", "/api/products"): ("Update product", None),
    ("PUT", "/api/products/price"): ("Update product prices", None),
    ("POST", "/api/production/norms"): ("Create production norm", None),
    ("PUT", "/api/production/norms"): ("Replace production norm", None),
    ("PATCH", "/api/production/norms"): ("Change production norm and its rows", None),
    ("POST", "/api/processing/norms"): ("Create processing norm", None),
    ("PUT", "/api/processing/norms"): ("Replace processing norm", None),
    ("PATCH", "/api/processing/norms"): ("Change processing norm and its rows", None),
    ("POST", "/api/clients"): ("Create client", None),
    ("PUT", "/api/clients"): ("Update client", None),
    ("POST", "/api/receipts"): ("Create purchase receipt", None),
    ("POST", "/api/orders"): ("Create sales document", None),
    ("POST", "/api/employee/timesheet"): ("Record employee working time", None),
    ("POST", "/api/customer-returns"): ("Create customer return", None),
    ("GET", "/api/receipts/{docNo}"): ("Get purchase receipt", None),
    ("GET", "/api/receipts/search"): ("Search purchase receipts", None),
    ("GET", "/api/products/{id}"): ("Get product", None),
    ("GET", "/api/products/search"): ("Search products", None),
    ("GET", "/api/products/qty/search"): ("Get stock quantities", None),
    ("GET", "/api/products/price/search"): ("Search product prices", None),
    ("GET", "/api/production/norms/{id}"): ("Get production norm", None),
    ("DELETE", "/api/production/norms/{id}"): ("Delete production norm", None),
    ("GET", "/api/production/norms/search"): ("Search production norms", None),
    ("GET", "/api/processing/norms/{id}"): ("Get processing norm", None),
    ("DELETE", "/api/processing/norms/{id}"): ("Delete processing norm", None),
    ("GET", "/api/processing/norms/search"): ("Search processing norms", None),
    ("GET", "/api/orders/{docNo}"): ("Get sales document", None),
    ("GET", "/api/orders/tracking/{docNo}"): ("Get sales document tracking", None),
    ("GET", "/api/orders/tracking/search"): ("Search sales document tracking", None),
    ("GET", "/api/orders/search"): ("Search sales documents", None),
    ("GET", "/api/orders/pdf/{docNo}"): ("Save sales document PDF", "everyday"),
    ("GET", "/api/employee/{id}"): ("Get employee", None),
    ("GET", "/api/customer-returns/{docNo}"): ("Get customer return", None),
    ("GET", "/api/customer-returns/search"): ("Search customer returns", None),
    ("GET", "/api/clients/{id}"): ("Get client", None),
    ("GET", "/api/clients/search"): ("Search clients", None),
    ("GET", "/api/clients/doc/search"): ("Search client payment documents", None),
    ("GET", "/api/clients/{id}/doc/search"): ("Search one client's payment documents", None),
}

#: What each step does, in one or two sentences (rights, repeats and warnings are added
#: by build_contracts.py from the specification).
DESCRIPTIONS: dict[str, str] = {
    "products_create": "Creates a product (goods, a service, a manufactured product, a kit…) "
    "with its code, name, groups, barcodes and extra fields.",
    "products_update": "Changes the product whose ID is given: the fields given are written.",
    "products_price_update": "Sets prices (kn0 purchase, kn1 wholesale, kn2 retail, kn3–kn5 "
    "additional, knmin minimum, knb base) of products found by ID or code, in the default "
    "warehouse or, with wh_all, in all warehouses.",
    "production_norms_create": "Creates a production norm: the product made, its quantity, "
    "the raw products used and the labour costs.",
    "production_norms_update": "Replaces the production norm whose ID is given with the one "
    "given, rows included.",
    "production_norms_patch": "Changes the production norm whose ID is given: header fields "
    "given are written; raw product and cost rows are added, changed or deleted.",
    "processing_norms_create": "Creates a processing norm: the input products, the output "
    "products with their cost share and the processing costs.",
    "processing_norms_update": "Replaces the processing norm whose ID is given with the one "
    "given, rows included.",
    "processing_norms_patch": "Changes the processing norm whose ID is given: header fields "
    "given are written; input, output and cost rows are added, changed or deleted.",
    "clients_create": "Creates a client with its code, name, company and VAT codes, roles "
    "(client_role: BUYER, SUPPLIER, CARRIER, INDIVIDUAL, B1–B10), address and payment terms.",
    "clients_update": "Changes a client named by its ID or code: the fields given are written.",
    "receipts_create": "Creates a purchase receipt (an incoming invoice) from a supplier with "
    "its product lines, VAT and totals.",
    "orders_create": "Creates a sales document — an invoice, an order or an offer (doc_type) — "
    "for a client with its product lines and totals.",
    "employee_timesheet_create": "Records an employee's (driver's) working time for a day, "
    "as the documentation's example shows: date, driverID, workTimeDay, workTimeNight.",
    "customer_returns_create": "Creates a customer return document with its product lines "
    "and totals.",
    "receipts_get": "Reads a purchase receipt by its document number.",
    "receipts_search": "Finds purchase receipts by ID, number, date, change time or warehouse.",
    "products_get": "Reads a product by its ID.",
    "products_search": "Finds products by ID, code, name, barcode, quantity, change time or "
    "warehouse.",
    "products_qty_search": "Reads stock quantities of products, by product or warehouse.",
    "products_price_search": "Reads product prices and costs, by product or warehouse.",
    "production_norms_get": "Reads a production norm by its ID.",
    "production_norms_delete": "Deletes the production norm whose ID is given.",
    "production_norms_search": "Finds production norms by ID, code, product, change time or "
    "warehouse.",
    "processing_norms_get": "Reads a processing norm by its ID.",
    "processing_norms_delete": "Deletes the processing norm whose ID is given.",
    "processing_norms_search": "Finds processing norms by ID, code or warehouse.",
    "orders_get": "Reads a sales document by its number.",
    "orders_tracking_get": "Reads the tracking of a sales document by its number.",
    "orders_tracking_search": "Finds sales document tracking by ID, document number or "
    "tracking number.",
    "orders_search": "Finds sales documents (invoices, orders, offers) by ID, number, "
    "registration number, date, change time or warehouse.",
    "orders_pdf_get": "Saves the PDF of a sales document, found by its number, as a workflow file.",
    "employee_get": "Reads an employee by ID.",
    "customer_returns_get": "Reads a customer return by its document number.",
    "customer_returns_search": "Finds customer returns by ID, number, registration number, "
    "dates, change time or warehouse.",
    "clients_get": "Reads a client by ID.",
    "clients_search": "Finds clients by ID, code, name, company code, e-mail, debt, change "
    "time or sales agent.",
    "clients_doc_search": "Finds clients' sales documents with what was charged and paid, by "
    "client code or company code, document, payment state, dates or warehouse.",
    "clients_id_doc_search": "Finds one client's sales documents (client_id) with what was "
    "charged and paid, by document, payment state, dates or warehouse.",
}

#: A path parameter whose input name would clash with a search field of the same step.
PATH_INPUT_NAMES = {("GET", "/api/clients/{id}/doc/search", "id"): "client_id"}

#: Writes that delete data as part of what they do, and the sentence saying so.
IRREVERSIBLE = {
    ("PUT", "/api/production/norms"): (
        "Agnum deletes the stored norm and writes the one given (its documented "
        '"delete old, write new"): rows left out are gone, and the step cannot undo it.'
    ),
    ("PUT", "/api/processing/norms"): (
        "Agnum deletes the stored norm and writes the one given (its documented "
        '"delete old, write new"): rows left out are gone, and the step cannot undo it.'
    ),
    ("PATCH", "/api/production/norms"): (
        'Rows given with rec_id and "delete": true are deleted, rows with rec_id are '
        "changed and rows without it are added; the step cannot undo it."
    ),
    ("PATCH", "/api/processing/norms"): (
        'Rows given with rec_id and "delete": true are deleted, rows with rec_id are '
        "changed and rows without it are added; the step cannot undo it."
    ),
}

#: A refused key proves a write changed nothing on every operation, documented or not:
#: the security filter answers before routing (checked without a key 2026-11-16: POST
#: /api/orders with {} → 403, and POST/PUT/DELETE/GET on /api/does-not-exist → 403),
#: so the specification's per-operation lists are incomplete here.
AUTH_REFUSALS = (401, 403)


@cache
def facts() -> dict[str, Any]:
    return json.loads(FACTS.read_text(encoding="utf-8"))


def operations() -> list[dict[str, Any]]:
    return list(facts()["operations"])


def schema(name: str) -> dict[str, Any]:
    return facts()["schemas"][name]


def operation_id(method: str, path: str) -> str:
    """create/update/patch/delete/get by the method; the path's words, a closing
    parameter dropped and an inner one kept by name (clients_id_doc_search)."""
    parts = [p for p in path.split("/") if p and p != "api"]
    words: list[str] = []
    for index, part in enumerate(parts):
        if part.startswith("{"):
            if index < len(parts) - 1:
                words.append(_snake(part[1:-1]))
            continue
        words.append(part.replace("-", "_"))
    last_is_param = parts[-1].startswith("{")
    suffix = {
        "POST": "create",
        "PUT": "update",
        "PATCH": "patch",
        "DELETE": "delete",
        "GET": "get" if last_is_param else "",
    }[method]
    return "_".join(words + ([suffix] if suffix else []))


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def kind(op: dict[str, Any]) -> str:
    method, path = op["method"], op["path"]
    if method == "GET":
        if path.startswith("/api/orders/pdf/"):
            return "pdf"
        return "search" if path.endswith("/search") else "get"
    return {"POST": "create", "PUT": "update", "PATCH": "patch", "DELETE": "delete"}[method]


WRITE_KINDS = frozenset({"create", "update", "patch", "delete"})


def danger(op: dict[str, Any]) -> str:
    if op["method"] == "DELETE":
        return "delete"
    return "irreversible" if (op["method"], op["path"]) in IRREVERSIBLE else ""


def body_schema(op: dict[str, Any]) -> dict[str, Any] | None:
    body = op.get("body")
    return body["schema"] if body else None


def objects(op: dict[str, Any]) -> str:
    """one / several (see the module); "" for a read."""
    if kind(op) not in WRITE_KINDS:
        return ""
    body = body_schema(op)
    if body is None:
        return "one"
    if body.get("type") == "array":
        return "several"
    name = body.get("ref")
    if not name:
        return "one"
    for spec in schema(name)["properties"].values():
        if spec.get("type") == "array" and (spec.get("items") or {}).get("ref"):
            return "several"
    return "one"


def no_effect(op: dict[str, Any]) -> list[int]:
    """The statuses after which this write certainly changed nothing: a refused key, and
    the documented statuses below."""
    documented = set(op["answers"])
    codes = set(AUTH_REFUSALS)
    strict = bool(danger(op))
    if "400" in documented and objects(op) == "one" and not strict:
        codes.add(400)
    if "404" in documented and kind(op) == "update" and objects(op) == "one" and not strict:
        # The record to change does not exist.
        codes.add(404)
    if "409" in documented and kind(op) == "delete":
        # Documented: "norm is in use and cannot be deleted".
        codes.add(409)
    return sorted(codes)


def plan() -> list[dict[str, Any]]:
    rows = []
    for op in operations():
        key = (op["method"], op["path"])
        title, group = TITLES[key]
        rows.append(
            {
                "method": op["method"],
                "path": op["path"],
                "operation_id": operation_id(*key),
                "title": title,
                "group": group or op["tag"],
                "tag": op["tag"],
                "kind": kind(op),
                "danger": danger(op),
                "objects": objects(op),
                "no_effect": no_effect(op) if kind(op) in WRITE_KINDS else [],
                "rights": op["rights"],
                "codes": sorted(op["answers"]),
                "release": RELEASE,
            }
        )
    return rows


__all__ = [
    "IRREVERSIBLE",
    "PATH_INPUT_NAMES",
    "TITLES",
    "WRITE_KINDS",
    "danger",
    "facts",
    "kind",
    "no_effect",
    "objects",
    "operation_id",
    "operations",
    "plan",
    "schema",
]
