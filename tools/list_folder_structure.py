"""Walk the app's folder tree and list what sits in each folder."""

import json

from tools._utils import load_units

NAVIGABLE_TYPES = {
    "Microflows$Microflow",
    "Microflows$Nanoflow",
    "Pages$Page",
    "Pages$Snippet",
    "Pages$Layout",
    "Pages$BuildingBlock",
    "Enumerations$Enumeration",
    "Constants$Constant",
    "JavaActions$JavaAction",
    "JavaScriptActions$JavaScriptAction",
    "ScheduledEvents$ScheduledEvent",
    "Projects$Folder",
    "Projects$Module",
}

_FRIENDLY_TYPES = {
    "Microflows$Microflow": "microflow",
    "Microflows$Nanoflow": "nanoflow",
    "Pages$Page": "page",
    "Pages$Snippet": "snippet",
    "Pages$Layout": "layout",
    "Projects$Folder": "folder",
    "Projects$Module": "module",
    "Enumerations$Enumeration": "enum",
    "Constants$Constant": "constant",
    "ScheduledEvents$ScheduledEvent": "scheduled_event",
    "JavaActions$JavaAction": "java_action",
    "JavaScriptActions$JavaScriptAction": "js_action",
}


def _path_of(by_id: dict, unit_id: str) -> list[str]:
    """Walk $ContainerID upwards to build the path from module down to unit."""
    parts = []
    current_id = unit_id
    visited = set()
    while current_id and current_id not in visited:
        visited.add(current_id)  # the dump is trusted but a cycle would hang us
        u = by_id.get(current_id)
        if not u:
            break
        if "Projects$Project" in u.get("$Type", ""):
            break  # the app root adds nothing to the path
        parts.append(u.get("name") or u.get("$QualifiedName", ""))
        current_id = u.get("$ContainerID")
    parts.reverse()
    return parts


def _friendly_type(unit_type: str) -> str:
    return _FRIENDLY_TYPES.get(unit_type, unit_type)


def list_folder_structure(module: str = None, folder: str = None,
                          project: str = None) -> dict:
    """Return the folder tree with the units inside each folder.

    Both filters are partial and case-insensitive.
    """
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    by_id = {u["$ID"]: u for u in units if "$ID" in u}

    tree: dict[tuple, list] = {}

    for unit in by_id.values():
        unit_type = unit.get("$Type", "")
        if unit_type not in NAVIGABLE_TYPES:
            continue

        path = _path_of(by_id, unit["$ID"])
        if not path:
            continue

        if module and module.lower() not in path[0].lower():
            continue

        if folder:
            # Look only at the intermediate segments: the first is the module
            # and the last is the unit itself.
            if not any(folder.lower() in p.lower() for p in path[1:-1]):
                continue

        key = tuple(path[:-1]) if len(path) > 1 else (path[0],)
        tree.setdefault(key, [])

        # Folders and modules are keys in the tree, not leaves under it.
        if unit_type not in ("Projects$Folder", "Projects$Module"):
            tree[key].append({
                "name": path[-1],
                "type": _friendly_type(unit_type),
            })

    structure = []
    for path_tuple in sorted(tree.keys()):
        contents = tree[path_tuple]
        if not contents:
            continue  # empty folder, nothing to report
        structure.append({
            "path": " > ".join(path_tuple),
            "total": len(contents),
            "units": sorted(contents, key=lambda u: (u["type"], u["name"])),
        })

    return {
        "success": True,
        "filters": {"module": module, "folder": folder},
        "total_folders": len(structure),
        "structure": structure,
    }


TOOL_DEFINITION = {
    "name": "list_folder_structure",
    "description": (
        "Return the folder tree of the Mendix app with the units inside each "
        "folder. Filter by module and/or folder name (partial, case-insensitive). "
        "Use it to see how the app is organised, or what lives in a given folder."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "module": {
                "type": "string",
                "description": "Module name to filter by (partial, case-insensitive).",
            },
            "folder": {
                "type": "string",
                "description": "Folder name to filter by (partial, case-insensitive).",
            },
        },
        "required": [],
    },
}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", default=None)
    parser.add_argument("--folder", default=None)
    parser.add_argument("--project", default=None)
    args = parser.parse_args()
    print(json.dumps(
        list_folder_structure(args.module, args.folder, args.project),
        indent=2, ensure_ascii=False,
    ))
