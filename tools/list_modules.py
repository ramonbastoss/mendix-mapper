"""List the app's modules, optionally filtered by origin."""

from tools._utils import load_units

VALID_SOURCES = ("all", "marketplace", "own")


def list_modules(source: str = "all", project: str = None) -> dict:
    """List the app's modules (Projects$Module).

    `source` filters by origin: 'all', 'marketplace' (fromAppStore is true) or
    'own' (fromAppStore absent or false).
    """
    if source not in VALID_SOURCES:
        return {
            "success": False,
            "error": f"Invalid source: '{source}'. Use one of {VALID_SOURCES}.",
        }

    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    modules = []
    for u in units:
        if u.get("$Type") != "Projects$Module":
            continue

        from_marketplace = bool(u.get("fromAppStore"))
        if source == "marketplace" and not from_marketplace:
            continue
        if source == "own" and from_marketplace:
            continue

        modules.append({
            "$ID": u.get("$ID"),
            "name": u.get("name"),
            # isThemeModule is the .mpr's historical name for the Studio Pro
            # option "Mark as UI resources module".
            "ui_resource": bool(u.get("isThemeModule")),
        })

    modules.sort(key=lambda m: (m["name"] or "").lower())

    return {"success": True, "total": len(modules), "modules": modules}


TOOL_DEFINITION = {
    "name": "list_modules",
    "description": (
        "List the modules of the Mendix app. 'source' filters by origin: 'all' "
        "(default), 'marketplace' (fromAppStore is true) or 'own'. Each entry "
        "carries the module $ID, its name, and ui_resource - whether it is marked "
        "as a UI resources module in Studio Pro.\n\n"
        "Known caveat: a few Marketplace modules do not carry the fromAppStore "
        "flag in the .mpr and will therefore be reported as 'own'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": list(VALID_SOURCES),
                "default": "all",
            },
        },
        "required": [],
    },
}


if __name__ == "__main__":
    import json
    print(json.dumps(list_modules(), indent=2, ensure_ascii=False))
