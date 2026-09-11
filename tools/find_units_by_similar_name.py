"""Fuzzy search over unit names."""

from difflib import get_close_matches

from tools._utils import cap_units, load_units, unit_names


def find_units_by_similar_name(name: str, limit: int = 5, project: str = None) -> dict:
    """Find the units whose names are closest to the one given.

    Use it when the exact name is unknown or possibly misspelled.
    """
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    # name -> unit. A unit contributes both its qualified and its simple name,
    # so either spelling can match.
    by_name = {}
    for unit in units:
        for candidate in unit_names(unit):
            by_name[candidate] = unit

    matches = get_close_matches(name, list(by_name.keys()), n=limit, cutoff=0.3)

    if not matches:
        return {"success": False, "error": f"No unit found close to: '{name}'"}

    return {
        "success": True,
        "total_found": len(matches),
        "units": cap_units([by_name[m] for m in matches]),
    }


TOOL_DEFINITION = {
    "name": "find_units_by_similar_name",
    "description": (
        "Fuzzy-search units of the Mendix app by name. Useful when the exact "
        "name is unknown or slightly wrong. Returns the closest matches."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Approximate unit name to look for",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results (default: 5)",
                "default": 5,
            },
        },
        "required": ["name"],
    },
}


if __name__ == "__main__":
    print(find_units_by_similar_name("ACT_Sve"))
