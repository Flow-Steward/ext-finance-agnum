"""Everyday steps: two documented operations in one step.

Searches are strict: every value given must match exactly (Agnum compares text
without regard to case, so this does too), every record Agnum returns is checked
again here, and a record that does not match means the search was not applied as
asked — the step then stops without writing anything. More than one match is a
conflict. A record is created only when nothing matches.

Agnum answers a search that finds nothing with 404 and its "not found" problem (the
specification's NotFound example: ``/api/products/search``, "product ABC123 not
found"); that is read as no records. A 404 without that problem body is an error.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from typing import Any

from . import errors, values
from .context import Context
from .errors import invalid
from .generic import prepare, search_records, spec, write_result


def _closed(context: Context, allowed: frozenset[str]) -> None:
    values.closed(context.operation_input, allowed | {"connection_ref"}, "The step input")


def _same(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    return str(a).strip().casefold() == str(b).strip().casefold()


def _has(record: Mapping[str, Any], field: str, value: str) -> bool:
    found = record.get(field)
    if isinstance(found, list):
        return any(_same(item, value) for item in found)
    return _same(found, value)


Rule = tuple[str, str, str]  # (search input, record field, value)


def _described(rules: list[Rule]) -> str:
    return " and ".join(f"{field} {value}" for _, field, value in rules)


def _find(context: Context, operation_id: str, rules: list[Rule], **extra: str) -> list[Any]:
    """Records of a search whose fields equal every value given."""
    query = {search: value for search, _, value in rules}
    query["filter_type"] = "eq"
    query.update(extra)
    records, text, _ = search_records(context, operation_id, query)
    if text is not None:
        raise _untrusted(operation_id, rules, "an answer that is not JSON")
    matched = [
        r for r in records if isinstance(r, Mapping) and all(_has(r, f, v) for _, f, v in rules)
    ]
    if len(matched) != len(records):
        raise _untrusted(
            operation_id, rules, f"{len(records) - len(matched)} records that do not match it"
        )
    return matched


def _untrusted(operation_id: str, rules: list[Rule], what: str) -> errors.ExtensionError:
    return errors.no_effect(
        errors.INVALID_RESPONSE,
        f"Agnum answered the search {spec(operation_id)['path']} by {_described(rules)} with "
        f"{what}: the search cannot be trusted, so nothing was created or changed",
        failure_class="provider",
        retryable=False,
    )


def _one(found: list[Any], what: str, rules: list[Rule]) -> Mapping[str, Any] | None:
    if len(found) > 1:
        ids = ", ".join(str(r.get("id")) for r in found[:5])
        raise errors.no_effect(
            errors.CONFLICT,
            f"{len(found)} {what}s match {_described(rules)} (IDs {ids}); they do not name "
            f"one {what}, so nothing was created or changed",
        )
    return found[0] if found else None


def _rules(
    given: Mapping[str, Any], keys: list[tuple[str, str, str]], max_length: int = 100
) -> list[Rule]:
    """(input, search input, record field) → the rules for the values given."""
    rules = []
    for name, search, field in keys:
        value = values.text(given.get(name), name, max_length=max_length)
        if value is not None:
            rules.append((search, field, value))
    if not rules:
        raise invalid(f"Give at least one of: {', '.join(k[0] for k in keys)}")
    return rules


def _single_keys(context: Context, operation_id: str, rules: list[Rule], what: str) -> None:
    """Before creating: no value alone may find a record (a near-duplicate otherwise)."""
    if len(rules) < 2:
        return
    hits = []
    for rule in rules:
        for record in _find(context, operation_id, [rule]):
            hits.append(f"{rule[1]} {rule[2]} matches {what} {record.get('id')}")
    if hits:
        raise errors.no_effect(
            errors.CONFLICT,
            f"No {what} has all of {', '.join(r[1] for r in rules)} as given, but "
            + "; ".join(hits[:10])
            + f" with other values: nothing was created. Check that {what} in Agnum",
        )


def _write(context: Context, operation_id: str, prepared: Any) -> dict[str, Any]:
    entry = spec(operation_id)
    answer = context.transport.write(
        prepared.method,
        prepared.path,
        prepared.body,
        shown=prepared.shown,
        no_effect=entry["no_effect"],
    )
    return write_result(context, entry, answer, prepared.shown)


# -- connection ----------------------------------------------------------------------------


def test_connection(context: Context) -> dict[str, Any]:
    _closed(context, frozenset())
    records, text, _ = search_records(
        context, "clients_search", {"code": "FLOWSTEWARD-CONNECTION-TEST", "filter_type": "eq"}
    )
    if text is not None:
        raise errors.no_effect(
            errors.INVALID_RESPONSE,
            "The address answered, but not as the Agnum API (the answer is not JSON); check "
            "the address: the API's root, for example https://server.example.lt/agnumAPIv2",
            failure_class="provider",
            retryable=False,
        )
    return {"connected": True, "warnings": []}


# -- clients -------------------------------------------------------------------------------

#: (input, search input, field of the client record)
CLIENT_KEYS = [
    ("code", "code", "code"),
    ("company_code", "client_code", "company_code"),
    ("email", "email", "email"),
]
FIND_CLIENT_INPUTS = frozenset(k[0] for k in CLIENT_KEYS)


def find_client(context: Context) -> dict[str, Any]:
    _closed(context, FIND_CLIENT_INPUTS)
    rules = _rules(context.operation_input, CLIENT_KEYS)
    client = _one(_find(context, "clients_search", rules), "client", rules)
    return {
        "found": client is not None,
        "client_id": client.get("id") if client else None,
        "client": client,
        "warnings": [],
    }


def client_input(operation_input: Mapping[str, Any]) -> dict[str, Any]:
    given = {k: v for k, v in operation_input.items() if k != "connection_ref"}
    if given.get("create_date") in (None, ""):
        given["create_date"] = dt.datetime.now(dt.timezone.utc).date().isoformat()
    return given


def find_or_create_client(context: Context) -> dict[str, Any]:
    given = client_input(context.operation_input)
    prepared = prepare("clients_create", given)  # checked before anything is asked for
    rules = _rules(given, CLIENT_KEYS)
    client = _one(_find(context, "clients_search", rules), "client", rules)
    if client is not None:
        return {"client_id": client.get("id"), "created": False, "client": client, "warnings": []}
    _single_keys(context, "clients_search", rules, "client")
    result = _write(context, "clients_create", prepared)
    data = result["data"]
    return {
        "client_id": result["id"],
        "created": True,
        "client": data if isinstance(data, Mapping) else None,
        "warnings": result["warnings"],
    }


# -- products ------------------------------------------------------------------------------

PRODUCT_KEYS = [("code", "code", "code"), ("barcode", "barcode", "barcode")]
FIND_PRODUCT_INPUTS = frozenset({"code", "barcode", "wh"})


def find_product(context: Context) -> dict[str, Any]:
    _closed(context, FIND_PRODUCT_INPUTS)
    given = context.operation_input
    rules = _rules(given, PRODUCT_KEYS)
    warehouse = values.text(given.get("wh"), "wh", max_length=10)
    extra = {"wh": warehouse} if warehouse else {}
    product = _one(_find(context, "products_search", rules, **extra), "product", rules)
    return {
        "found": product is not None,
        "product_id": product.get("id") if product else None,
        "product": product,
        "warnings": [],
    }


# -- documents -----------------------------------------------------------------------------


def _existing(
    context: Context, search_id: str, number_field: str, doc_no: str
) -> list[Mapping[str, Any]]:
    """Documents Agnum has with this number (checked again here)."""
    rule = (number_field, "doc_no", doc_no)
    return _find(context, search_id, [rule])


def create_sales_document_if_new(context: Context) -> dict[str, Any]:
    prepared = prepare("orders_create", context.operation_input)
    doc_no = prepared.body["doc_no"]
    found = _existing(context, "orders_search", "order_no", doc_no)
    if found:
        ids = ", ".join(str(d.get("id")) for d in found[:5])
        raise errors.no_effect(
            errors.CONFLICT,
            f"Agnum already has sales document {doc_no} (ID {ids}); nothing was created",
        )
    return _write(context, "orders_create", prepared)


SUPPLIER_KEYS = ("id", "code", "company_code", "email")


def _same_supplier(given: Mapping[str, Any], record: Mapping[str, Any]) -> bool | None:
    """True / False, or None when the receipt's supplier cannot be compared."""
    client = record.get("client")
    if not isinstance(client, Mapping):
        return None
    compared = [k for k in SUPPLIER_KEYS if given.get(k) is not None and client.get(k) is not None]
    if not compared:
        return None
    return any(_same(given[k], client[k]) for k in compared)


def create_purchase_receipt_if_new(context: Context) -> dict[str, Any]:
    prepared = prepare("receipts_create", context.operation_input)
    doc_no = prepared.body["doc_no"]
    supplier = prepared.body.get("client") or {}
    warnings = []
    for record in _existing(context, "receipts_search", "receipt_no", doc_no):
        same = _same_supplier(supplier, record)
        if same is False:
            warnings.append(
                f"Agnum has receipt {doc_no} (ID {record.get('id')}) from another supplier"
            )
            continue
        why = "from the same supplier" if same else "whose supplier cannot be compared"
        raise errors.no_effect(
            errors.CONFLICT,
            f"Agnum already has purchase receipt {doc_no} {why} (ID {record.get('id')}); "
            "nothing was created",
        )
    result = _write(context, "receipts_create", prepared)
    result["warnings"] = warnings + result["warnings"]
    return result


COMPOSITES: dict[str, Callable[[Context], dict[str, Any]]] = {
    "test_connection": test_connection,
    "find_client": find_client,
    "find_or_create_client": find_or_create_client,
    "find_product": find_product,
    "create_sales_document_if_new": create_sales_document_if_new,
    "create_purchase_receipt_if_new": create_purchase_receipt_if_new,
}
COMPOSITE_WRITES = frozenset(
    {"find_or_create_client", "create_sales_document_if_new", "create_purchase_receipt_if_new"}
)


def _validate_client(operation_input: Mapping[str, Any]) -> None:
    given = client_input(operation_input)
    prepare("clients_create", given)
    _rules(given, CLIENT_KEYS)


def _validate(operation_id: str, given: Mapping[str, Any]) -> None:
    prepare(operation_id, given)


#: Input checks of the everyday writes, without calling Agnum (also the test-run check).
VALIDATORS: dict[str, Callable[[Mapping[str, Any]], None]] = {
    "find_or_create_client": _validate_client,
    "create_sales_document_if_new": lambda given: _validate("orders_create", given),
    "create_purchase_receipt_if_new": lambda given: _validate("receipts_create", given),
}
#: Inputs of the everyday steps (the writes take their generated step's inputs).
INPUTS: dict[str, frozenset[str]] = {
    "test_connection": frozenset(),
    "find_client": FIND_CLIENT_INPUTS,
    "find_product": FIND_PRODUCT_INPUTS,
}

__all__ = ["COMPOSITES", "COMPOSITE_WRITES", "INPUTS", "VALIDATORS"]
