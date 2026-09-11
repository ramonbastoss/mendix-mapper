"""Full-text search across the serialized units."""

import json

from tools._utils import load_units


def search_text(
    query: str,
    module: str = None,
    unit_type: str = None,
    case_sensitive: bool = False,
    project: str = None,
) -> dict:
    """Find units whose serialized JSON contains the given text.

    This is the catch-all search: a label on a page, a caption on a button, a
    hardcoded string inside a microflow, an attribute name. It returns identity
    fields only, never the matching units' full JSON.
    """
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    if not query:
        return {"success": False, "error": "Provide the 'query' parameter."}

    needle = query if case_sensitive else query.lower()

    found = []
    for unit in units:
        if module:
            qname = unit.get("$QualifiedName", "")
            name = unit.get("name", "")
            unit_module = qname.split(".")[0] if "." in qname else ""
            if (module.lower() not in unit_module.lower()
                    and module.lower() not in name.lower()):
                continue

        if unit_type and unit_type.lower() not in (unit.get("$Type") or "").lower():
            continue

        serialized = json.dumps(unit, ensure_ascii=False)
        if not case_sensitive:
            serialized = serialized.lower()

        if needle in serialized:
            found.append({
                "id": unit.get("$ID"),
                "type": unit.get("$Type"),
                "name": unit.get("$QualifiedName") or unit.get("name"),
            })

    return {
        "success": True,
        "query": query,
        "total_found": len(found),
        "units": found,
    }


TOOL_DEFINITION = {
    "name": "search_text",
    "description": (
        "Search the Mendix app for units whose JSON contains a given string. "
        "This is what replaces grep on a codebase that is stored as binary: page "
        "labels, button captions, hardcoded strings in microflows, attribute "
        "names. Optional filters narrow it to one module or one $Type. "
        "Case-insensitive unless told otherwise."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Text to look for inside the units' JSON.",
            },
            "module": {
                "type": "string",
                "description": "Restrict to one module (partial, case-insensitive). Optional.",
            },
            "unit_type": {
                "type": "string",
                "description": "Restrict to a $Type, e.g. 'Pages$Page' (partial, case-insensitive). Optional.",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Match case exactly. Default: false.",
            },
        },
        "required": ["query"],
    },
}


if __name__ == "__main__":
    import pprint
    pprint.pprint(search_text("Save and close"))
    pprint.pprint(search_text("Save and close", unit_type="Pages$Page"))
