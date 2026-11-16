"""Step inputs built from the pinned specification's schemas.

A *node* is what runtime/params.py checks one value against: its wire name, its
input name, its kind and its limits. Kinds: text, date, datetime, when (a date or a
date and time), decimal, integer, boolean, object, object_list, text_list,
integer_list, opaque (a body the specification gives only an example of).
"""

from __future__ import annotations

from typing import Any

import plan

INT32 = 2**31 - 1
#: The largest record ID and whole number this extension sends.
MAX_ID = 10**15
MAX_ROWS = 500
DEFAULT_TEXT = 1000

#: Records that must name one of these fields (each text of the specification says so).
ONE_OF = {
    "NewReceiptProduct": ["id", "code"],
    "ProductPrices": ["product_id", "product_code"],
}
#: Search fields whose wire value is a number or a fixed choice.
SEARCH_KINDS: dict[tuple[str, str], dict[str, Any]] = {
    ("ClientSearch", "debt"): {"kind": "decimal"},
    ("ProductSearch", "quant"): {"kind": "decimal"},
    ("ProductQtySearch", "qty"): {"kind": "decimal"},
    ("ClientOrdersSearch", "paid"): {"kind": "text", "max_length": 1, "allowed": ["y", "n"]},
}
#: Search parameters that are not filters.
SEARCH_CONTROLS = {"filter_type", "order", "limit", "offset", "limit/offset", "fields"}
#: The sales tracking search documents its parameters only as "text values"; it takes
#: the fields of the Tracking record it returns (draft aside).
TRACKING_FIELDS = ["id", "doc_no", "track_no"]


def input_name(wire: str) -> str:
    return wire.replace("-", "_").replace("/", "_")


def _number_bounds(spec: dict[str, Any], node: dict[str, Any]) -> None:
    for key, target in (("minimum", "minimum"), ("maximum", "maximum")):
        if key in spec:
            node[target] = spec[key]


def field_node(
    name: str,
    spec: dict[str, Any],
    *,
    required: bool,
    mode: str,
    root: bool,
) -> dict[str, Any] | None:
    """The node of one schema field for a request of ``mode`` (create, update, patch),
    or None when the field is not sent (read-only, or a PATCH-only marker)."""
    if spec.get("readOnly"):
        keep = (mode == "patch" and name == "rec_id") or (
            root and mode == "update" and name == "id"
        )
        if not keep:
            return None
    if name == "delete" and mode != "patch":
        return None
    if root and mode == "create" and name == "id":
        # A new record's ID is Agnum's to give (documented for norms: "Ignored in POST").
        return None
    node: dict[str, Any] = {"name": name, "input": input_name(name), "required": required}
    if spec.get("description"):
        node["description"] = spec["description"][:300]
    if spec.get("deprecated"):
        node["deprecated"] = True
    if name == "rec_id":
        node.update(kind="integer", minimum=1, maximum=MAX_ID)
        return node
    if "ref" in spec:
        child = plan.schema(spec["ref"])
        node.update(kind="object", children=children(spec["ref"], child, mode=mode))
        if spec["ref"] in ONE_OF:
            node["one_of"] = ONE_OF[spec["ref"]]
        return node
    kind = spec.get("type")
    if kind == "array":
        items = spec.get("items") or {}
        node["max_items"] = int(spec.get("maxItems") or MAX_ROWS)
        if items.get("ref"):
            ref = items["ref"]
            node.update(kind="object_list", children=children(ref, plan.schema(ref), mode=mode))
            if ref in ONE_OF:
                node["one_of"] = ONE_OF[ref]
        elif items.get("type") == "integer":
            node.update(kind="integer_list", item=_scalar("integer", items))
        else:
            node.update(kind="text_list", item=_scalar("string", items))
        if spec.get("uniqueItems"):
            node["unique"] = True
        return node
    node.update(_scalar(kind or "string", spec))
    if node["kind"] == "integer" and _names_a_record(name):
        node["minimum"] = max(1, int(node.get("minimum", 1)))
    return node


def _names_a_record(name: str) -> bool:
    """id, product_id, empl_id, … — an Agnum record ID, always positive."""
    return name == "id" or name.endswith("_id") or name.endswith("ID")


def _scalar(kind: str, spec: dict[str, Any]) -> dict[str, Any]:
    if kind == "string":
        fmt = spec.get("format")
        if fmt == "date":
            return {"kind": "date"}
        if fmt == "date-time":
            return {"kind": "datetime"}
        node: dict[str, Any] = {
            "kind": "text",
            "max_length": int(spec.get("maxLength") or DEFAULT_TEXT),
        }
        if spec.get("minLength"):
            node["min_length"] = int(spec["minLength"])
        if spec.get("enum"):
            node["allowed"] = list(spec["enum"])
        return node
    if kind == "number":
        node = {"kind": "decimal"}
        _number_bounds(spec, node)
        return node
    if kind == "integer":
        limit = INT32 if spec.get("format") == "int32" else MAX_ID
        node = {"kind": "integer", "minimum": -limit, "maximum": limit}
        _number_bounds(spec, node)
        return node
    if kind == "boolean":
        return {"kind": "boolean"}
    raise SystemExit(f"no node for type {kind!r}")


def children(name: str, schema: dict[str, Any], *, mode: str) -> list[dict[str, Any]]:
    required = set(schema["required"]) if mode != "patch" else set()
    out = []
    for field, spec in schema["properties"].items():
        node = field_node(field, spec, required=field in required, mode=mode, root=False)
        if node is not None:
            out.append(node)
    return out


def body_nodes(op: dict[str, Any], row: dict[str, Any]) -> dict[str, Any] | None:
    """The request body as one root node, or None for a request without a body."""
    body = plan.body_schema(op)
    if body is None:
        return None
    mode = {"create": "create", "update": "update", "patch": "patch"}[row["kind"]]
    if body.get("type") == "array":
        ref = body["items"]["ref"]
        return {
            "name": "prices",
            "input": "prices",
            "kind": "object_list",
            "required": True,
            "max_items": MAX_ROWS,
            "min_items": 1,
            "children": [
                c
                for c in children(ref, plan.schema(ref), mode=mode)
                if not c["name"].startswith("sv_")
            ],
            "one_of": ONE_OF.get(ref, []),
            "description": "One row per product: the product (product_id or product_code) "
            "and the prices to set.",
        }
    if "ref" not in body:
        # Documented only by an example (the timesheet): its fields as the example shows them.
        return {
            "name": "",
            "input": "",
            "kind": "object",
            "required": True,
            "children": _example_nodes(op),
        }
    schema = plan.schema(body["ref"])
    required = set(schema["required"]) if mode != "patch" else set()
    nodes = []
    for field, spec in schema["properties"].items():
        node = field_node(field, spec, required=field in required, mode=mode, root=True)
        if node is not None:
            nodes.append(node)
    if mode in {"update", "patch"} and any(n["name"] == "id" for n in nodes):
        if row["danger"] or mode == "patch" or "id" in schema["required"]:
            for node in nodes:
                if node["name"] == "id":
                    node.update(required=True, minimum=1)
    root: dict[str, Any] = {
        "name": "",
        "input": "",
        "kind": "object",
        "required": True,
        "children": nodes,
    }
    if mode == "update" and body["ref"] == "NewClient":
        root["one_of"] = ["id", "code"]
    return root


def _example_nodes(op: dict[str, Any]) -> list[dict[str, Any]]:
    example = op["body"]["schema"].get("example") or {}
    nodes = []
    for name, value in example.items():
        node: dict[str, Any] = {"name": name, "input": input_name(name), "required": True}
        if isinstance(value, bool):
            node["kind"] = "boolean"
        elif isinstance(value, int):
            node.update(kind="integer", minimum=0, maximum=INT32)
        elif isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-":
            node["kind"] = "date"
        else:
            node.update(kind="text", max_length=100, min_length=1)
        node["description"] = f"As in the documentation's example ({value})."
        nodes.append(node)
    return nodes


def path_nodes(op: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = []
    for param in op["parameters"]:
        if param["in"] != "path":
            continue
        name = param["name"]
        node: dict[str, Any] = {
            "name": name,
            "input": plan.PATH_INPUT_NAMES.get((op["method"], op["path"], name), input_name(name)),
            "required": True,
            "description": param["description"] or "",
        }
        if param["schema"].get("type") == "integer":
            node.update(kind="integer", minimum=1, maximum=MAX_ID)
        else:
            node.update(kind="text", max_length=100, min_length=1)
        nodes.append(node)
    return nodes


def query_nodes(op: dict[str, Any]) -> list[dict[str, Any]]:
    """Plain query parameters (not the search object)."""
    nodes = []
    for param in op["parameters"]:
        if param["in"] != "query" or param["name"] == "params":
            continue
        nodes.append(
            {
                "name": param["name"],
                "input": input_name(param["name"]),
                "required": param["required"],
                "kind": "text",
                "max_length": 200,
                "min_length": 1,
                "description": param["description"] or param["name"],
            }
        )
    return nodes


def _search_field(schema_name: str, name: str, spec: dict[str, Any]) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": name,
        "input": input_name(name),
        "required": False,
        "description": spec.get("description", "")[:300],
    }
    override = SEARCH_KINDS.get((schema_name, name))
    text = spec.get("description", "")
    if override:
        node.update(override)
    elif spec.get("type") == "integer":
        node.update(kind="integer", minimum=0, maximum=MAX_ID)
    elif "HH:mm:ss" in text:
        node["kind"] = "when"
    elif "yyyy-MM-dd" in text:
        node["kind"] = "date"
    else:
        node.update(kind="text", max_length=200, min_length=1)
    return node


def search_spec(op: dict[str, Any]) -> dict[str, Any] | None:
    """The filters, sort, paging and rules of a search step."""
    param = next((p for p in op["parameters"] if p["name"] == "params"), None)
    if param is None:
        return None
    ref = param["schema"].get("ref")
    if ref is None:
        # Text values with no schema (the tracking search): the Tracking record's fields.
        tracking = plan.schema("Tracking")["properties"]
        fields = [_search_field("Tracking", f, tracking[f]) for f in TRACKING_FIELDS]
        return {
            "schema": None,
            "fields": fields,
            "filter_type": [],
            "order": [],
            "paging": False,
            "fields_choice": [],
            "at_least_one": True,
            "combined_paging": False,
        }
    schema = plan.schema(ref)
    props = schema["properties"]
    fields = [_search_field(ref, n, s) for n, s in props.items() if n not in SEARCH_CONTROLS]
    paging = "limit" in props and "offset" in props
    answer_item = _answer_item(op)
    return {
        "schema": ref,
        "fields": fields,
        "filter_type": list(props.get("filter_type", {}).get("enum", [])),
        "order": list(props.get("order", {}).get("enum", [])),
        "paging": paging,
        "max_limit": int(props["limit"].get("maximum", 250)) if paging else None,
        "fields_choice": (
            list(plan.schema(answer_item)["properties"])
            if "fields" in props and answer_item
            else []
        ),
        "at_least_one": "At least one search parameter is required" in schema["description"],
        "combined_paging": "limit/offset" in props,
    }


def _answer_item(op: dict[str, Any]) -> str | None:
    schema = (op["answers"].get("200") or {}).get("schema") or {}
    if schema.get("type") == "array":
        return (schema.get("items") or {}).get("ref")
    return schema.get("ref")


__all__ = ["body_nodes", "field_node", "path_nodes", "query_nodes", "search_spec"]
