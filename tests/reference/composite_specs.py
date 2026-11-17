"""The everyday steps (runtime/composites.py): names, descriptions, inputs and outputs.

The create-if-new steps and Find or create client take the same inputs as the
generated step they write through, so their forms and checks cannot drift apart.
"""

from __future__ import annotations

import copy
from typing import Any

WARNINGS = {
    "name": "warnings",
    "value_type": "array",
    "schema": {
        "type": "array",
        "items": {"type": "string"},
        "description": "What the step could not do fully or what to check.",
    },
}
NULL_TEXT = ["string", "null"]
NULL_INT = ["integer", "null"]


def out(name: str, value_type: str, kind: Any, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "value_type": value_type,
        "schema": {"type": kind, "description": description},
    }


def record(name: str, description: str) -> dict[str, Any]:
    return out(name, "object", ["object", "null"], description)


def text(name: str, title: str, description: str, *, max_length: int) -> dict[str, Any]:
    return {
        "name": name,
        "value_type": "text",
        "required": False,
        "schema": {
            "type": "string",
            "title": title,
            "description": description,
            "maxLength": max_length,
        },
    }


CLIENT_KEYS = [
    text("code", "Client code", "The client's code in Agnum.", max_length=20),
    text("company_code", "Company code", "The company's registration code.", max_length=15),
    text("email", "E-mail", "The client's e-mail address.", max_length=100),
]
PRODUCT_KEYS = [
    text("code", "Product code", "The product's code in Agnum.", max_length=25),
    text("barcode", "Barcode", "One of the product's barcodes.", max_length=25),
    text("wh", "Warehouse", "A warehouse code, to look in that warehouse only.", max_length=10),
]
CREATED = [
    out("id", "text", ["integer", "string", "null"], "The new document's ID in Agnum."),
    record(
        "data",
        "What Agnum returned: the ID, the document number and the registration "
        "number as Agnum gives them.",
    ),
    WARNINGS,
]
DOCUMENT_CHECK = (
    " Before writing, the step looks the number up and stops with a conflict, writing "
    "nothing, when Agnum already has a document with it{also}. When the lookup cannot be "
    "trusted (Agnum returns documents with other numbers, or an empty answer instead of a "
    "list or its not-found answer), it also stops without writing."
)


def _inputs(generated: dict[str, list[dict[str, Any]]], operation_id: str) -> list[dict[str, Any]]:
    return copy.deepcopy(generated[operation_id])


def steps(generated: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The everyday steps, given the generated steps' inputs by operation id."""
    client_inputs = _inputs(generated, "clients_create")
    for item in client_inputs:
        if item["name"] == "create_date":
            item["required"] = False
            item["schema"]["description"] = (
                "The client's creation date in Agnum, YYYY-MM-DD. Default: today (UTC)."
            )
    return [
        {
            "operation_id": "test_connection",
            "display_name": "Test connection",
            "kind": "source",
            "calls": ["GET /api/clients/search"],
            "description": "Checks that the address answers as the Agnum API and that the "
            "key is accepted, with one client search that finds nothing. Needs the key right "
            "READ_KL or READ_ALL.",
            "inputs": [],
            "outputs": [
                out("connected", "boolean", "boolean", "True when Agnum accepted the key."),
                WARNINGS,
            ],
        },
        {
            "operation_id": "find_client",
            "display_name": "Find client",
            "kind": "source",
            "calls": ["GET /api/clients/search"],
            "description": "Finds the one client whose code, company code and e-mail equal "
            "the ones given (give at least one). Every client Agnum returns is checked again "
            "here; more than one match is a conflict. Needs READ_KL or READ_ALL.",
            "inputs": copy.deepcopy(CLIENT_KEYS),
            "outputs": [
                out("found", "boolean", "boolean", "True when one client matches."),
                out("client_id", "integer", NULL_INT, "The client's ID."),
                record("client", "The client as Agnum returns it."),
                WARNINGS,
            ],
        },
        {
            "operation_id": "find_or_create_client",
            "display_name": "Find or create client",
            "kind": "action",
            "calls": ["GET /api/clients/search", "POST /api/clients"],
            "description": "Finds the client by the code, company_code and e-mail given (at "
            "least one) and creates it with the fields given when none matches. When one of "
            "those values alone finds a client with different other values, the step stops "
            "with a conflict instead of creating a near-duplicate; more than one match is a "
            "conflict too. Needs READ_KL and SAVE_KL (or READ_ALL and SAVE_ALL).",
            "inputs": client_inputs,
            "outputs": [
                out("client_id", "integer", NULL_INT, "The client's ID."),
                out("created", "boolean", "boolean", "True when the client was created."),
                record("client", "The client as Agnum returns it."),
                WARNINGS,
            ],
        },
        {
            "operation_id": "find_product",
            "display_name": "Find product",
            "kind": "source",
            "calls": ["GET /api/products/search"],
            "description": "Finds the one product whose code and barcode equal the ones given "
            "(give at least one), optionally in one warehouse. Every product Agnum returns is "
            "checked again here; more than one match is a conflict. Needs READ_PRK or "
            "READ_ALL.",
            "inputs": copy.deepcopy(PRODUCT_KEYS),
            "outputs": [
                out("found", "boolean", "boolean", "True when one product matches."),
                out("product_id", "integer", NULL_INT, "The product's ID."),
                record("product", "The product as Agnum returns it."),
                WARNINGS,
            ],
        },
        {
            "operation_id": "create_sales_document_if_new",
            "display_name": "Create sales document if new",
            "kind": "action",
            "calls": ["GET /api/orders/search", "POST /api/orders"],
            "description": "Creates a sales document — an invoice, an order or an offer "
            "(doc_type) — once per document number (doc_no)."
            + DOCUMENT_CHECK.format(also="")
            + " Needs READ_VSK and SAVE_VSK (or READ_ALL and SAVE_ALL).",
            "inputs": _inputs(generated, "orders_create"),
            "outputs": copy.deepcopy(CREATED),
        },
        {
            "operation_id": "create_purchase_receipt_if_new",
            "display_name": "Create purchase receipt if new",
            "kind": "action",
            "calls": ["GET /api/receipts/search", "POST /api/receipts"],
            "description": "Creates a purchase receipt once per supplier and document number "
            "(doc_no)."
            + DOCUMENT_CHECK.format(
                also=" from the same supplier, or one whose supplier cannot be told apart"
            )
            + " It reads every page of receipts with that number. Suppliers are told apart "
            "by id, code and company code only: e-mail alone never decides, and keys that "
            "contradict each other stop the step."
            + " Needs READ_VKS and SAVE_VKS (or READ_ALL and SAVE_ALL).",
            "inputs": _inputs(generated, "receipts_create"),
            "outputs": copy.deepcopy(CREATED),
        },
    ]


__all__ = ["CLIENT_KEYS", "PRODUCT_KEYS", "WARNINGS", "steps"]
