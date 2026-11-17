"""Runners of the generated steps: one documented Agnum operation each.

What each step accepts and how its answer is read comes from runtime/registry.json,
built from the pinned specification by tests/reference/build_contracts.py.

Answers are read as the specification documents them and no further: a record or a
list as JSON; an answer documented only as "string", or not at all, is returned as
JSON when it is JSON and as text otherwise, with no fields made up; the sales
document PDF (documented as an example with a base64 ``file``) is saved as a workflow
file and never put into the step output.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

from . import budget, errors, files, values
from .context import Context
from .errors import invalid
from .params import check, fields
from .transport import MAX_PDF_ANSWER_BYTES, Answer, unknown

REGISTRY_FILE = Path(__file__).resolve().parent / "registry.json"
PRICE_NOTE = (
    "Agnum updates only the price levels enabled in its API settings and ignores the others "
    "without saying so: check the prices in Agnum"
)
SEARCH_CONTROLS = ("filter_type", "order", "fields", "limit", "offset")
_PDF_MAGIC = b"%PDF-"
_FILE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


@cache
def registry() -> dict[str, dict[str, Any]]:
    data = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return dict(data["operations"])


def spec(operation_id: str) -> dict[str, Any]:
    return registry()[operation_id]


# -- the request -----------------------------------------------------------------------------


@dataclass
class Prepared:
    method: str
    path: str
    query: list[tuple[str, str]]
    body: Any
    shown: str


def wire_text(value: Any) -> str:
    """A checked query value as text (Decimal plainly, booleans as JSON writes them)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return values.wire(value)
    return str(value)


def _inputs_of(entry: Mapping[str, Any]) -> set[str]:
    names = {n["input"] for n in entry["path_params"]} | {n["input"] for n in entry["query"]}
    search = entry.get("search")
    if search:
        names |= {n["input"] for n in search["fields"]}
        names |= {"filter_type"} if search["filter_type"] else set()
        names |= {"order"} if search["order"] else set()
        names |= {"fields"} if search["fields_choice"] else set()
        names |= {"limit", "offset"} if search["paging"] else set()
    body = entry.get("body")
    if body:
        names |= {body["input"]} if body["input"] else {n["input"] for n in body["children"]}
    return names


def prepare(operation_id: str, operation_input: Mapping[str, Any]) -> Prepared:
    """The checked request of a generated step (also its test-run check)."""
    entry = spec(operation_id)
    given = {k: v for k, v in operation_input.items() if k != "connection_ref"}
    values.closed(given, frozenset(_inputs_of(entry)), "The step input")
    path = entry["path"]
    for node in entry["path_params"]:
        value = given.get(node["input"])
        if value is None or value == "":
            raise invalid(f"{node['input']} is required")
        text = wire_text(check(node, value, node["input"]))
        path = path.replace("{" + node["name"] + "}", quote(text, safe=""))
    query: list[tuple[str, str]] = []
    plain = {n["input"] for n in entry["query"]}
    for name, value in fields(
        entry["query"], {k: v for k, v in given.items() if k in plain}
    ).items():
        query.append((name, wire_text(value)))
    search = entry.get("search")
    if search:
        filters = fields(
            search["fields"],
            {k: v for k, v in given.items() if k in {n["input"] for n in search["fields"]}},
        )
        if search["at_least_one"] and not filters and not query:
            raise invalid(
                "Give at least one filter: " + ", ".join(n["input"] for n in search["fields"])
            )
        query += [(name, wire_text(value)) for name, value in filters.items()]
        query += _search_controls(search, given)
    body: Any = None
    root = entry.get("body")
    if root:
        if root["input"]:
            value = given.get(root["input"])
            if value is None or value == []:
                raise invalid(f"{root['input']} is required")
            body = check(root, value, root["input"])
        else:
            rest = {k: v for k, v in given.items() if k in {n["input"] for n in root["children"]}}
            body = fields(root["children"], rest)
            one_of = root.get("one_of") or []
            if one_of and not any(name in body for name in one_of):
                raise invalid(f"Give {' or '.join(one_of)}")
    shown = f"{entry['method']} {entry['path']}"
    return Prepared(entry["method"], path, query, body, shown)


def _search_controls(search: Mapping[str, Any], given: Mapping[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if given.get("filter_type") not in (None, ""):
        choice = values.choice(
            given["filter_type"], "filter_type", {v: v for v in search["filter_type"]}
        )
        out.append(("filter_type", str(choice)))
    if given.get("order") not in (None, ""):
        choice = values.choice(given["order"], "order", {v: v for v in search["order"]})
        out.append(("order", str(choice)))
    if given.get("fields") not in (None, "", []):
        chosen = given["fields"]
        if not isinstance(chosen, list) or not all(
            isinstance(f, str) and f in search["fields_choice"] for f in chosen
        ):
            raise invalid(f"fields must be a list of: {', '.join(search['fields_choice'])}")
        out.append(("fields", ",".join(dict.fromkeys(chosen))))
    if given.get("limit") not in (None, ""):
        limit = values.int_in(
            given["limit"], "limit", default=search["max_limit"], low=1, high=search["max_limit"]
        )
        out.append(("limit", str(limit)))
    if given.get("offset") not in (None, ""):
        offset = values.int_in(given["offset"], "offset", default=0, low=0, high=10**9)
        out.append(("offset", str(offset)))
    return out


# -- reading answers -------------------------------------------------------------------------


def _value(context: Context, answer: Answer) -> tuple[Any, str | None]:
    """(JSON value, None), or (None, text) when the answer is not JSON, or (None, None)
    when it is empty."""
    if answer.empty:
        return None, None
    try:
        return context.transport.json(answer), None
    except ValueError:
        return None, context.transport.text(answer, 20_000)


def search_records(
    context: Context, operation_id: str, given: Mapping[str, Any], *, proof: bool = False
) -> tuple[list[Any], str | None, list[str]]:
    """(records, text, warnings) of a search; Agnum's 404 "not found" is no records.

    With ``proof`` the answer decides whether something is created: only a JSON list or
    Agnum's "not found" problem counts, and an empty answer or JSON ``null`` is refused
    (a plain search step shows it as no records with a warning).
    """
    prepared = prepare(operation_id, given)
    entry = spec(operation_id)
    answer = context.transport.read(
        prepared.path, prepared.query, shown=prepared.shown, missing_ok=True
    )
    if answer.status == 404:
        return [], None, []
    value, text = _value(context, answer)
    warnings: list[str] = []
    if isinstance(value, list):
        return value, None, warnings
    if value is None and text is None:
        if proof:
            raise errors.no_effect(
                errors.INVALID_RESPONSE,
                f"Agnum answered {prepared.shown} without content (HTTP {answer.status}): that "
                "does not show that nothing matches, so nothing was created or changed",
                failure_class="provider",
                retryable=False,
            )
        return [], None, ["Agnum answered without content"]
    if text is not None:
        if entry["answer"] != "any":
            raise errors.no_effect(
                errors.INVALID_RESPONSE,
                f"Agnum answered {prepared.shown} with something that is not JSON",
                failure_class="provider",
                retryable=False,
            )
        return [], text, ["Agnum's answer is not JSON; it is returned as text"]
    warnings.append("Agnum returned one record rather than a list; it is the only item")
    return [value], None, warnings


def _save_pdf(context: Context, value: Any, shown: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not isinstance(value.get("file"), str):
        raise errors.no_effect(
            errors.INVALID_RESPONSE,
            f"Agnum answered {shown} without a file",
            failure_class="provider",
            retryable=False,
        )
    encoded = re.sub(r"\s+", "", value["file"])
    if encoded.startswith("data:") and "," in encoded:
        encoded = encoded.split(",", 1)[1]
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise errors.no_effect(
            errors.INVALID_RESPONSE,
            f"Agnum answered {shown} with a file that is not base64",
            failure_class="provider",
            retryable=False,
        ) from None
    if not content.startswith(_PDF_MAGIC):
        raise errors.no_effect(
            errors.INVALID_RESPONSE,
            f"Agnum answered {shown} with a file that is not a PDF",
            failure_class="provider",
            retryable=False,
        )
    if len(content) > files.MAX_DOWNLOAD_BYTES:
        raise errors.no_effect(errors.RESPONSE_TOO_LARGE, "The PDF is larger than 40 MB")
    handle = files.save(
        context,
        content,
        binding_key=files.DOWNLOAD_BINDING,
        content_type=files.DOWNLOAD_CONTENT_TYPE,
        what="The PDF",
    )
    name = value.get("file_name")
    name = _FILE_NAME_RE.sub("_", name).strip()[:200] if isinstance(name, str) else ""
    doc_no = value.get("doc_no")
    ident = value.get("id")
    return {
        "file": handle,
        "file_name": name or None,
        "doc_no": errors.safe_text(doc_no, 100) if doc_no is not None else None,
        "document_id": ident
        if isinstance(ident, (int, str)) and not isinstance(ident, bool)
        else None,
        "size_bytes": len(content),
        "warnings": [],
    }


def write_result(
    context: Context, entry: Mapping[str, Any], answer: Answer, shown: str
) -> dict[str, Any]:
    """The outputs of a write Agnum confirmed (2xx)."""
    value, text = _value(context, answer)
    warnings: list[str] = []
    if entry["answer"] == "boolean" and value is not True:
        raise unknown(
            f"Agnum answered {shown} with {json.dumps(value) if text is None else 'text'} "
            "instead of true, so it is not known what was changed; check in Agnum before "
            "running the step again",
            errors.INVALID_RESPONSE,
            answer.status,
        )
    if text is not None:
        if entry["answer"] != "any":
            warnings.append(
                f"Agnum confirmed {shown} (HTTP {answer.status}) but its answer is not JSON; "
                "it is returned as text"
            )
    elif value is None and entry["answer"] != "boolean":
        warnings.append(f"Agnum confirmed {shown} (HTTP {answer.status}) without an answer")
    if entry.get("price_note"):
        warnings.append(PRICE_NOTE)
    ident = value.get("id") if isinstance(value, Mapping) else None
    if isinstance(ident, bool) or not isinstance(ident, (int, str)):
        ident = None
    result: dict[str, Any] = {"id": ident, "data": value, "warnings": warnings}
    if entry["answer"] == "any":
        result["text"] = text
    return result


# -- running -----------------------------------------------------------------------------------


def run(context: Context, operation_id: str) -> dict[str, Any]:
    entry = spec(operation_id)
    given = {k: v for k, v in context.operation_input.items() if k != "connection_ref"}
    kind = entry["kind"]
    transport = context.transport
    if kind == "search":
        items, text, warnings = search_records(context, operation_id, given)
        result: dict[str, Any] = {"items": items, "count": len(items), "warnings": warnings}
        if entry["answer"] == "any":
            result["text"] = text
        budget.ensure_fits(result)
        return result
    prepared = prepare(operation_id, given)
    if kind == "pdf":
        answer = transport.read(
            prepared.path, prepared.query, shown=prepared.shown, limit=MAX_PDF_ANSWER_BYTES
        )
        value, _ = _value(context, answer)
        return _save_pdf(context, value, prepared.shown)
    if kind == "get":
        answer = transport.read(prepared.path, prepared.query, shown=prepared.shown)
        value, text = _value(context, answer)
        warnings = []
        if text is not None and entry["answer"] != "any":
            raise errors.no_effect(
                errors.INVALID_RESPONSE,
                f"Agnum answered {prepared.shown} with something that is not JSON",
                failure_class="provider",
                retryable=False,
            )
        if value is None and text is None:
            warnings.append("Agnum answered without content")
        result = {"data": value, "warnings": warnings}
        if entry["answer"] == "any":
            result["text"] = text
        budget.ensure_fits(result)
        return result
    answer = transport.write(
        prepared.method,
        prepared.path,
        prepared.body,
        shown=prepared.shown,
        no_effect=entry["no_effect"],
    )
    result = write_result(context, entry, answer, prepared.shown)
    if kind == "delete":
        node = entry["path_params"][0]
        return {
            "deleted_id": int(check(node, given[node["input"]], node["input"])),
            "data": result["data"],
            "warnings": result["warnings"],
        }
    return result


__all__ = ["Prepared", "prepare", "registry", "run", "search_records", "spec", "write_result"]
