"""List the app-level user roles."""

from tools._utils import load_units


def list_user_roles(project: str = None) -> dict:
    """Return the app's user roles, read from the Security$ProjectSecurity unit."""
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    project_security = next(
        (u for u in units if u.get("$Type") == "Security$ProjectSecurity"), None
    )

    if not project_security:
        return {
            "success": False,
            "error": "No Security$ProjectSecurity unit found in the dump.",
        }

    roles = [
        {
            "$ID": role.get("$ID"),
            "name": role.get("name"),
            "description": role.get("description"),
            "moduleRoles": role.get("moduleRoles", []),
            "manageableRoles": role.get("manageableRoles", []),
        }
        for role in project_security.get("userRoles", [])
    ]

    return {"success": True, "total": len(roles), "userRoles": roles}


TOOL_DEFINITION = {
    "name": "list_user_roles",
    "description": (
        "Return the user roles of the Mendix app, with the module roles each one "
        "aggregates and the roles it is allowed to manage. Use it to see which "
        "access profiles exist and what they are composed of."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


if __name__ == "__main__":
    import json
    print(json.dumps(list_user_roles(), indent=2, ensure_ascii=False))
