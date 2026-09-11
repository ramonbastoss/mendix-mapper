"""Find units by their exact name."""

from tools._utils import cap_units, load_units, unit_names


def find_unit_by_name(name: str, project: str = None) -> dict:
    """Find units whose name matches exactly.

    A qualified name ('MyModule.ACT_Save') identifies exactly one unit.
    A simple name ('ACT_Save') may match units in several modules.
    """
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    found = [u for u in units if name in unit_names(u)]

    if not found:
        return {"success": False, "error": f"No unit found with the exact name: '{name}'"}

    if len(found) == 1:
        return {"success": True, "total_found": 1, "units": cap_units(found)}

    return {
        "success": True,
        "total_found": len(found),
        "warning": (
            f"'{name}' is a simple name and was found in {len(found)} different modules. "
            f"Use the $QualifiedName (e.g. 'ModuleName.{name}') to target one unit."
        ),
        "units": cap_units(found),
    }


TOOL_DEFINITION = {
    "name": "find_unit_by_name",
    "description": (
        "Find units in the Mendix app by exact name. Accepts a simple name "
        "('ACT_Save') or a qualified one ('MyModule.ACT_Save'). Simple names may "
        "match several modules, so prefer the qualified form when the module is known."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Unit name. Prefer 'ModuleName.UnitName' to avoid ambiguity.",
            },
        },
        "required": ["name"],
    },
}


if __name__ == "__main__":
    print(find_unit_by_name("MyModule.ACT_Save"))
