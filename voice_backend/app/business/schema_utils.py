from __future__ import annotations

from copy import deepcopy
from typing import Any


def to_groq_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    transformed = deepcopy(schema)
    _walk(transformed)
    return transformed


def _walk(node: Any) -> None:
    if isinstance(node, dict):
        node_type = node.get("type")
        properties = node.get("properties")
        if node_type == "object" and isinstance(properties, dict):
            node["additionalProperties"] = False
            node["required"] = list(properties.keys())
            for value in properties.values():
                _walk(value)

        for key in ("items", "anyOf", "allOf", "oneOf", "prefixItems"):
            value = node.get(key)
            if isinstance(value, dict):
                _walk(value)
            elif isinstance(value, list):
                for item in value:
                    _walk(item)

    elif isinstance(node, list):
        for item in node:
            _walk(item)
