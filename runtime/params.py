"""Step inputs checked against the specification's fields, and turned into the request.

Each node comes from runtime/registry.json (built from the pinned specification by
tests/reference/build_contracts.py): its wire name, its input name, its kind,
whether it is required, its allowed values and limits. Unknown fields are refused,
not dropped; numbers stay Decimal to the wire.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from . import values
from .errors import invalid

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)
_DATETIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2}):(\d{2})$", re.ASCII)


def _date(value: Any, what: str) -> str:
    return values.iso_date(value, what).isoformat()


def _datetime(value: Any, what: str, *, date_alone: bool) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if date_alone and _DATE_RE.fullmatch(text):
        return _date(text, what)
    match = _DATETIME_RE.fullmatch(text)
    if not match:
        shape = "YYYY-MM-DD or YYYY-MM-DD HH:MM:SS" if date_alone else "YYYY-MM-DD HH:MM:SS"
        raise invalid(f"{what} must be written {shape}")
    _date(match.group(1), what)
    hour, minute, second = (int(match.group(i)) for i in (2, 3, 4))
    try:
        dt.time(hour, minute, second)
    except ValueError:
        raise invalid(f"{what} is not a real time of day") from None
    return text


def _integer(node: Mapping[str, Any], value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise invalid(f"{what} must be a whole number")
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        value = int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,19}", value.strip()):
        value = int(value.strip())
    if not isinstance(value, int):
        raise invalid(f"{what} must be a whole number")
    low, high = node.get("minimum"), node.get("maximum")
    if (low is not None and value < low) or (high is not None and value > high):
        raise invalid(f"{what} must be a whole number from {low} to {high}")
    return value


def _decimal(node: Mapping[str, Any], value: Any, what: str) -> Decimal:
    number = values.number(value, what)
    low, high = node.get("minimum"), node.get("maximum")
    if low is not None and number < Decimal(str(low)):
        raise invalid(f"{what} may not be less than {low}")
    if high is not None and number > Decimal(str(high)):
        raise invalid(f"{what} may not be more than {high}")
    return number


def check(node: Mapping[str, Any], value: Any, what: str) -> Any:
    """One value checked against its node; the value to send."""
    kind = node["kind"]
    if kind == "text":
        text = values.text(value, what, max_length=int(node["max_length"]), multi_line=True)
        if text is None:
            raise invalid(f"{what} is empty")
        if len(text) < int(node.get("min_length") or 0):
            raise invalid(f"{what} is too short")
        allowed = node.get("allowed")
        if allowed and text not in allowed:
            raise invalid(f"{what} must be one of: {', '.join(allowed)}")
        return text
    if kind == "date":
        return _date(value, what)
    if kind == "datetime":
        return _datetime(value, what, date_alone=False)
    if kind == "when":
        return _datetime(value, what, date_alone=True)
    if kind == "decimal":
        return _decimal(node, value, what)
    if kind == "integer":
        return _integer(node, value, what)
    if kind == "boolean":
        if not isinstance(value, bool):
            raise invalid(f"{what} must be true or false")
        return value
    if kind in {"text_list", "integer_list"}:
        return _scalar_list(node, value, what)
    if kind == "object":
        if not isinstance(value, Mapping):
            raise invalid(f"{what} must be an object")
        return record(node, value, what)
    if kind == "object_list":
        limit = int(node["max_items"])
        if not isinstance(value, list) or len(value) > limit:
            raise invalid(f"{what} must be a list of at most {limit} objects")
        if len(value) < int(node.get("min_items") or 0):
            raise invalid(f"{what} needs at least {node['min_items']} item")
        out = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, Mapping):
                raise invalid(f"{what} item {index} must be an object")
            out.append(record(node, item, f"{what} item {index}"))
        return out
    raise invalid(f"{what} has a kind this extension does not know")  # pragma: no cover


def _scalar_list(node: Mapping[str, Any], value: Any, what: str) -> list[Any]:
    limit = int(node["max_items"])
    if not isinstance(value, list) or len(value) > limit:
        raise invalid(f"{what} must be a list of at most {limit} values")
    out = [check(node["item"], item, f"{what} item {i}") for i, item in enumerate(value, 1)]
    if node.get("unique") and len(set(out)) != len(out):
        raise invalid(f"{what} repeats a value")
    return out


def record(node: Mapping[str, Any], given: Mapping[str, Any], what: str) -> dict[str, Any]:
    """An object checked against the node's children: closed, typed."""
    children = node.get("children") or []
    out = fields(children, given, what)
    one_of = node.get("one_of") or []
    if one_of and not any(name in out for name in one_of):
        raise invalid(f"{what} needs {' or '.join(one_of)}")
    if any(c["name"] == "delete" for c in children):
        _row_rules(out, what)
    return out


def _row_rules(row: Mapping[str, Any], what: str) -> None:
    """A PATCH row: deleting takes rec_id and nothing else; adding takes no rec_id."""
    if row.get("delete") is True:
        if "rec_id" not in row:
            raise invalid(f"{what} deletes a row: give its rec_id")
        extra = sorted(set(row) - {"rec_id", "delete"})
        if extra:
            raise invalid(
                f"{what} deletes row {row['rec_id']}: give only rec_id and delete, not "
                + ", ".join(extra)
            )


def fields(
    nodes: list[Mapping[str, Any]], given: Mapping[str, Any], where: str = ""
) -> dict[str, Any]:
    """The wire fields for ``given`` (keyed by input name at the top, wire name below)."""
    key = "input" if not where else "name"
    by_key = {n[key]: n for n in nodes}
    unknown = sorted(str(k) for k in given if k not in by_key)
    if unknown:
        raise invalid(f"{where or 'The step input'} has unknown fields: {', '.join(unknown[:10])}")
    out: dict[str, Any] = {}
    for node in nodes:
        value = given.get(node[key])
        what = f"{where}.{node['name']}" if where else node["input"]
        if value is None or value == "" or value == []:
            if node.get("required"):
                raise invalid(f"{what} is required")
            continue
        out[node["name"]] = check(node, value, what)
    return out


__all__ = ["check", "fields", "record"]
