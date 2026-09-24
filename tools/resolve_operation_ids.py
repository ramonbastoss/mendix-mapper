"""Decode the opaque operation ids the Mendix client sends to the runtime."""

import datetime
import json
import os

from tools._utils import deployment_model_path

_OPERATIONS_FILE = "operations.json"

_NOT_FOUND = (
    "Not present in this build's operations.json. Ids are derived from the "
    "operation itself and do survive a rebuild, so this is usually a capture "
    "taken against a meaningfully different version of the app — or an "
    "operation whose shape has changed since. Compare 'built_at' with when the "
    "traffic was captured; if they disagree, rebuild at the matching commit."
)


def _load_index(project: str) -> tuple[dict, str, str]:
    """Return ({operationId: operation}, path, ISO mtime) for one project."""
    path = deployment_model_path(project, _OPERATIONS_FILE)
    with open(path, encoding="utf-8") as f:
        operations = json.load(f)

    built_at = datetime.datetime.fromtimestamp(
        os.path.getmtime(path)
    ).isoformat(timespec="seconds")

    return {op["operationId"]: op for op in operations if "operationId" in op}, path, built_at


def _summarize(op: dict, verbose: bool) -> dict:
    """Reduce one operation to what identifies it, or all of it when verbose."""
    constants = op.get("constants") or {}

    entry = {"found": True, "operation_type": op.get("operationType")}

    # Both callMicroflow and retrieveByMicroflow name their microflow here;
    # the latter is a widget data source, so it also carries page and widget.
    if constants.get("MicroflowName"):
        entry["microflow"] = constants["MicroflowName"]
    if constants.get("PageName"):
        entry["page"] = constants["PageName"]
    if constants.get("WidgetName"):
        entry["widget"] = constants["WidgetName"]

    # The generic object operations (commit, rollback, delete, create) name no
    # microflow and no page. ObjectType is the only thing that tells them apart.
    if constants.get("ObjectType"):
        entry["entity"] = constants["ObjectType"]

    if verbose:
        entry["parameters"] = op.get("parameters") or {}
        entry["constants"] = constants
        entry["allowed_user_role_sets"] = op.get("allowedUserRoleSets") or []

    return entry


def resolve_operation_ids(ids: list[str], verbose: bool = False,
                          project: str = None) -> dict:
    """Resolve client operation ids to what they actually invoke.

    The ids are opaque by construction: there is no transform from a microflow's
    name, qualified name or stableId that produces one, so the only way back is
    this lookup table. It ships in the build as deployment/model/operations.json.

    Ids that do not resolve are reported individually rather than failing the
    call, because the usual cause — a capture taken against a different build —
    affects only some of them.
    """
    if not ids:
        return {"success": False, "error": "No ids given."}

    try:
        index, path, built_at = _load_index(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"{_OPERATIONS_FILE} is not valid JSON: {e}"}

    results = {}
    for id in ids:
        op = index.get(id)
        results[id] = _summarize(op, verbose) if op else {
            "found": False,
            "error": _NOT_FOUND,
        }

    resolved = sum(1 for r in results.values() if r["found"])

    return {
        "success": True,
        "source_file": path,
        "built_at": built_at,
        "total": len(results),
        "resolved": resolved,
        "unresolved": len(results) - resolved,
        "results": results,
    }


TOOL_DEFINITION = {
    "name": "resolve_operation_ids",
    "description": (
        "Resolve the opaque operation ids the Mendix client sends to the runtime "
        "into the microflow, page or widget they invoke.\n\n"
        "Where these come from: in the browser's network tab, calls to /xas/ take "
        "two shapes. 'executeaction' already names the microflow in "
        "params.actionname and needs no help. 'runtimeOperation' instead carries "
        "a top-level 'operationId' — a sibling of 'params', not inside it — an "
        "opaque 22-character string such as "
        "'Ab3xK9/uQ1W+mNpZrS4tLg'. This tool is what turns those into names, and "
        "is therefore how a recorded user journey becomes a list of microflows.\n\n"
        "Resolves every operation type, not only microflow calls: a data grid "
        "loading its rows comes back as a 'retrieve' naming its page and widget. "
        "Ids that do not resolve are flagged individually.\n\n"
        "Reads deployment/model/operations.json, which a build writes. Ids are "
        "content-derived and survive a rebuild, so a recent file resolves "
        "captures from neighbouring commits; the response still carries "
        "'built_at' so a capture from a far older version can be spotted."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "operationId values, exactly as they appear in the payload "
                    "(they are base64 and may contain '/' and '+')"
                ),
            },
            "verbose": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Also return each operation's parameters, full constants "
                    "(including a retrieve's XPath) and allowed user role sets. "
                    "Off by default: a journey can be hundreds of calls."
                ),
            },
        },
        "required": ["ids"],
    },
}


if __name__ == "__main__":
    # Pick a real id out of whatever project is configured, so the smoke test
    # exercises both paths on any app rather than hardcoding one app's ids.
    index, _, _ = _load_index(None)
    a_microflow = next(
        (id for id, op in index.items()
         if op.get("operationType") == "callMicroflow"),
        None,
    )
    print(json.dumps(
        resolve_operation_ids([a_microflow, "this-id-does-not-exist"]),
        indent=2, ensure_ascii=False,
    ))
