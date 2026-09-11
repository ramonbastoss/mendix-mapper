"""Return one unit's full JSON by its $ID."""

from tools._utils import cap_unit, load_units


def find_unit_by_id(id: str, project: str = None) -> dict:
    """Return the complete JSON of the unit carrying the given $ID."""
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    for unit in units:
        if unit.get("$ID") == id:
            return {"success": True, "unit": cap_unit(unit)}

    return {"success": False, "error": f"No unit found with ID: '{id}'"}


TOOL_DEFINITION = {
    "name": "find_unit_by_id",
    "description": (
        "Return the full JSON of a single unit by its exact $ID (a UUID). "
        "Use it when you already have an ID and want the unit's details."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Unit UUID, e.g. '3fa85f64-5717-4562-b3fc-2c963f66afa6'",
            },
        },
        "required": ["id"],
    },
}


if __name__ == "__main__":
    print(find_unit_by_id("3fa85f64-5717-4562-b3fc-2c963f66afa6"))
