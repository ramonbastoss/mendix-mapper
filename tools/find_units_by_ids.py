"""Resolve several unit IDs in one pass."""

from tools._utils import cap_unit, load_units


def find_units_by_ids(ids: list[str], project: str = None) -> dict:
    """Resolve a list of $IDs to units.

    IDs that do not resolve are reported individually rather than failing the
    whole call, so a single bad ID does not hide the good ones.
    """
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    index = {unit["$ID"]: unit for unit in units if "$ID" in unit}

    results = {}
    for id in ids:
        if id in index:
            results[id] = {"found": True, "unit": cap_unit(index[id])}
        else:
            results[id] = {"found": False, "error": f"ID '{id}' not found in the dump"}

    return {"success": True, "results": results}


TOOL_DEFINITION = {
    "name": "find_units_by_ids",
    "description": (
        "Resolve a list of unit $IDs at once. IDs that do not exist come back "
        "flagged individually instead of failing the call. Use it to look up "
        "several units in one round trip."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "UUIDs of the units to look up",
            },
        },
        "required": ["ids"],
    },
}


if __name__ == "__main__":
    print(find_units_by_ids([
        "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "this-id-does-not-exist",
    ]))
