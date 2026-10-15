"""Reverse references: which units point at a given unit."""

import json

from tools._utils import excluded_count, load_units, mark_excluded, unit_names


def find_units_that_use(name: str = None, id: str = None, project: str = None) -> dict:
    """Return every unit that references the given one.

    The search looks for the target's $QualifiedName inside the serialized JSON
    of every other unit, which is how references are stored.
    """
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    if not name and not id:
        return {"success": False, "error": "Provide at least 'name' or 'id'."}

    target = None
    for unit in units:
        if id and unit.get("$ID") == id:
            target = unit
            break
        if name and name in unit_names(unit):
            target = unit
            break

    if not target:
        return {"success": False, "error": f"Unit not found: '{name or id}'"}

    qualified_name = target.get("$QualifiedName")
    if not qualified_name:
        return {
            "success": False,
            "error": (
                f"Unit '{target.get('name')}' has no $QualifiedName, so its "
                "references cannot be traced."
            ),
        }

    # Search with the surrounding quotes so a name is not matched as a prefix of
    # a longer one: '"MyModule.Save"' will not match '"MyModule.SaveDraft"'.
    needle = f'"{qualified_name}"'

    found = []
    for unit in units:
        if unit.get("$ID") == target.get("$ID"):
            continue  # skip the target itself
        if needle in json.dumps(unit):
            found.append(mark_excluded({
                "name": unit.get("$QualifiedName") or unit.get("name"),
                "type": unit.get("$Type"),
                "id": unit.get("$ID"),
            }, unit))

    result = {
        "success": True,
        "target_unit": qualified_name,
        "total_found": len(found),
        "used_by": found,
    }

    # This tool answers "what breaks if I change this", and an excluded caller
    # does not break - it is not deployed. Left in the list because a reference
    # from a document someone may re-enable is still worth seeing, but counted
    # separately so the number is not read as live impact.
    excluded = excluded_count(found)
    if excluded:
        result["excluded_count"] = excluded
        result["note"] = (
            f"{excluded} of these are excluded from the build and are marked "
            '"excluded": true. They do not run, so they are not live impact.'
        )
    return result


TOOL_DEFINITION = {
    "name": "find_units_that_use",
    "description": (
        "Return every unit in the Mendix app that references the given unit. "
        "This is the impact question: which microflows, pages or entities depend "
        "on it, and therefore what a change might break. Accepts an exact name "
        "or an $ID."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact unit name, e.g. 'MyModule.ACT_Save'",
            },
            "id": {
                "type": "string",
                "description": "Unit UUID, e.g. '3fa85f64-5717-4562-b3fc-2c963f66afa6'",
            },
        },
        "required": [],
    },
}


if __name__ == "__main__":
    print(find_units_that_use(name="MyModule.ACT_Save"))
