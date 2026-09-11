"""Read a module's security unit and its module roles."""

from tools._utils import load_units


def get_module_security(module: str, project: str = None) -> dict:
    """Return the Security$ModuleSecurity of a module, by module name.

    Includes the security unit's own $ID, which is what you need to track role
    changes through git history.
    """
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    module_unit = next(
        (u for u in units
         if u.get("$Type") == "Projects$Module" and u.get("name") == module),
        None,
    )

    if not module_unit:
        available = [u.get("name") for u in units if u.get("$Type") == "Projects$Module"]
        return {
            "success": False,
            "error": f"Module '{module}' not found.",
            "available_modules": available,
        }

    module_id = module_unit["$ID"]

    # The module's security unit is the one whose container is the module.
    module_security = next(
        (u for u in units
         if u.get("$Type") == "Security$ModuleSecurity"
         and u.get("$ContainerID") == module_id),
        None,
    )

    if not module_security:
        return {
            "success": False,
            "error": f"No Security$ModuleSecurity found for module '{module}'.",
            "module_id": module_id,
        }

    roles = [
        {
            "$ID": role.get("$ID"),
            "name": role.get("name"),
            "description": role.get("description", ""),
        }
        for role in module_security.get("moduleRoles", [])
    ]

    return {
        "success": True,
        "module": module,
        "module_id": module_id,
        "module_security_id": module_security["$ID"],
        "total_roles": len(roles),
        "moduleRoles": roles,
    }


TOOL_DEFINITION = {
    "name": "get_module_security",
    "description": (
        "Return a module's security unit and the module roles defined in it, by "
        "module name. Also returns the security unit's $ID, which is what lets "
        "you follow role changes through git history. If the module is not found, "
        "the available module names come back with the error."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "module": {
                "type": "string",
                "description": "Exact module name. Use list_modules if unsure.",
            },
        },
        "required": ["module"],
    },
}


if __name__ == "__main__":
    import json
    import sys
    module = sys.argv[1] if len(sys.argv) > 1 else "Administration"
    print(json.dumps(get_module_security(module), indent=2, ensure_ascii=False))
