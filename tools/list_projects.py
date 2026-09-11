"""List the projects configured on this machine."""

import os

from tools._utils import _load_config


def list_projects() -> dict:
    """List every project in projects.json, with the state of its dump.

    Doubles as the setup check: it is the one call that tells you whether the
    configuration is readable and whether each project still needs a dump.
    """
    try:
        config = _load_config()
    except FileNotFoundError as e:
        return {"success": False, "error": str(e)}

    default = config.get("default")
    projects = config.get("projects", {})

    result = []
    for key, proj in projects.items():
        dump_path = proj.get("dump_path", "")
        fieldbook_path = proj.get("fieldbook_path", "")
        result.append({
            "key": key,
            "name": proj.get("name", key),
            "is_default": key == default,
            "dump_exists": os.path.exists(dump_path) if dump_path else False,
            "mpr_path": proj.get("mpr_path", ""),
            "dump_path": dump_path,
            "fieldbook_path": fieldbook_path,
            "fieldbook_exists": os.path.isdir(fieldbook_path) if fieldbook_path else False,
        })

    return {
        "success": True,
        "total": len(result),
        "default_project": default,
        "projects": result,
    }


TOOL_DEFINITION = {
    "name": "list_projects",
    "description": (
        "List the Mendix projects configured on this machine. Shows each "
        "project key (used as the 'project' argument of every other tool), its "
        "display name, which one is the default, and whether its dump has been "
        "generated. Call this first when you do not know which project to use."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


if __name__ == "__main__":
    import json
    print(json.dumps(list_projects(), indent=2, ensure_ascii=False))
