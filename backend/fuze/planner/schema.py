"""Provider-enforced syntax; build_plan and structural_issues enforce semantics."""
from ..parts.categories import CATEGORIES
from .topology import INPUT_SOURCES


def object_schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def array(items: dict) -> dict:
    return {"type": "array", "items": items}


PLAN_SCHEMA = object_schema({
    "input_source": {"type": "string", "enum": [*INPUT_SOURCES, "unresolved"]},
    "input_voltage": {"type": ["number", "null"]},
    "temp_range": array({"type": "integer"}),
    "current_margin": {"type": "number"},
    "priority": {"type": "string", "enum": ["cost", "size", "availability"]},
    "min_stock": {"type": "integer"},
    "ambient_c": {"type": "integer"},
    "lifetime_hours": {"type": ["number", "null"]},
    "supply_capacity_mah": {"type": ["number", "null"]},
    "slots": array(object_schema({
        "id": {"type": "string"}, "label": {"type": "string"},
        "tier": {"type": "string", "enum": ["power", "core", "peripherals", "passives"]},
        "pinned": {"type": "boolean"}, "query": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "topology": {"type": ["string", "null"], "enum": ["ldo", "buck", "boost", "buck-boost", None]},
    })),
    "rails": array(object_schema({"id": {"type": "string"}, "voltage": {"type": "number"},
                                  "source": {"type": "string"}, "members": array({"type": "string"})})),
    "links": array(array({"type": "string"})),
})
