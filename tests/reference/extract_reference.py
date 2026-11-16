#!/usr/bin/env python3
"""Pin the facts this extension uses from the Agnum DB API specification.

Source: the OpenAPI 3.1 document the vendor's Swagger UI shows
(https://srv1.elit.lt/agnumAPIv2/v3/api-docs). It is not committed: download it to
tests/reference/sources/agnum-api-docs.json, then run from the bundle root:

    python3 tests/reference/extract_reference.py

The script refuses a document whose SHA-256 is not the pinned one, so a changed
specification is noticed rather than silently picked up. Output:
tests/reference/agnum_api.json — every operation (method, path, parameters, body,
documented answers, required rights) and every schema field, with descriptions
shortened. Nothing is guessed here; build_contracts.py decides what the steps do.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "sources" / "agnum-api-docs.json"
OUTPUT = HERE / "agnum_api.json"
URL = "https://srv1.elit.lt/agnumAPIv2/v3/api-docs"
SHA256 = "1d5c67dbfc3983624ec1932989d8adfdebb572f02c76e8497067b78aa4bbc527"
DOWNLOADED = "2026-11-16"
METHODS = ("get", "post", "put", "patch", "delete")
#: Schema keywords kept per field (everything else is presentation).
FIELD_KEYS = (
    "type",
    "format",
    "enum",
    "maxLength",
    "minLength",
    "minimum",
    "maximum",
    "maxItems",
    "minItems",
    "readOnly",
    "deprecated",
    "default",
    "uniqueItems",
)
_RIGHTS_RE = re.compile(r"\*\*Required rights \(any of\):\*\*\s*(.+)$", re.M)
_RIGHT_RE = re.compile(r"`([A-Z0-9_]+)`")


def short(text: Any, limit: int = 400) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def ref_name(ref: str) -> str:
    """The schema a local $ref points at (JSON pointer, percent-decoded)."""
    prefix = "#/components/schemas/"
    if not ref.startswith(prefix):
        raise SystemExit(f"unexpected $ref {ref!r}")
    return unquote(ref[len(prefix) :]).replace("~1", "/").replace("~0", "~")


def field(spec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "$ref" in spec:
        out["ref"] = ref_name(spec["$ref"])
    for key in FIELD_KEYS:
        if key in spec:
            out[key] = spec[key]
    if "items" in spec:
        out["items"] = field(spec["items"])
    if isinstance(spec.get("additionalProperties"), dict):
        out["values"] = field(spec["additionalProperties"])
    if spec.get("description"):
        out["description"] = short(spec["description"])
    if "example" in spec:
        out["example"] = spec["example"]
    return out


def content_schema(content: dict[str, Any] | None) -> dict[str, Any] | None:
    if not content:
        return None
    media, body = next(iter(content.items()))
    schema = body.get("schema")
    if schema is None:
        return {"media": media}
    return {"media": media, **field(schema)}


def operation(method: str, path: str, op: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    description = op.get("description") or ""
    rights_line = _RIGHTS_RE.search(description)
    rights = _RIGHT_RE.findall(rights_line.group(1)) if rights_line else []
    answers: dict[str, Any] = {}
    for code, answer in op.get("responses", {}).items():
        if "$ref" in answer:
            name = answer["$ref"].rsplit("/", 1)[-1]
            answer = doc["components"]["responses"][name]
        answers[code] = {
            "description": short(answer.get("description"), 120),
            "schema": content_schema(answer.get("content")),
        }
    parameters = []
    for param in op.get("parameters", []):
        parameters.append(
            {
                "in": param["in"],
                "name": param["name"],
                "required": bool(param.get("required")),
                "style": param.get("style"),
                "explode": param.get("explode"),
                "schema": field(param.get("schema") or {}),
                "description": short(param.get("description"), 200),
            }
        )
    body = op.get("requestBody")
    return {
        "method": method.upper(),
        "path": path,
        "vendor_operation_id": op.get("operationId"),
        "tag": (op.get("tags") or [""])[0],
        "summary": short(op.get("summary"), 200),
        "description": short(_RIGHTS_RE.sub("", description), 1500),
        "rights": rights,
        "parameters": parameters,
        "body": (
            {"required": bool(body.get("required")), "schema": content_schema(body["content"])}
            if body
            else None
        ),
        "answers": answers,
    }


def extract(raw: bytes) -> dict[str, Any]:
    doc = json.loads(raw)
    operations = [
        operation(method, path, ops[method], doc)
        for path, ops in doc["paths"].items()
        for method in METHODS
        if method in ops
    ]
    schemas = {}
    for name, schema in doc["components"]["schemas"].items():
        schemas[name] = {
            "description": short(schema.get("description"), 300),
            "required": list(schema.get("required") or []),
            "properties": {k: field(v) for k, v in (schema.get("properties") or {}).items()},
        }
    security = doc["components"]["securitySchemes"]
    return {
        "source": {
            "url": URL,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "downloaded": DOWNLOADED,
            "openapi": doc["openapi"],
            "title": doc["info"]["title"],
            "api_version": doc["info"]["version"],
            "servers": doc.get("servers"),
            "auth": {k: {"in": v.get("in"), "name": v.get("name")} for k, v in security.items()},
        },
        "operations": operations,
        "schemas": schemas,
    }


def main() -> None:
    raw = SOURCE.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != SHA256:
        sys.exit(f"{SOURCE.name} is {digest}, not the pinned {SHA256}: the specification changed")
    OUTPUT.write_text(
        json.dumps(extract(raw), indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
