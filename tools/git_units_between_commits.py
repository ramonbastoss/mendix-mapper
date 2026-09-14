"""Which microflows, nanoflows and pages really changed between two refs.

The distinction this exists for: Studio Pro records the position of everything on
the canvas inside the unit, so dragging a box two centimetres produces a diff
that looks exactly as large as rewriting the logic. Comparing the paths of the
differences separates the two.
"""

from tools.git_diff_commits import collect_diffs

# Keys under which Studio Pro stores where something sits on the canvas. A change
# confined to these means somebody moved a box, not that behaviour changed.
POSITIONAL_KEYS = {
    "Location", "RelativeMiddlePoint", "ChildConnection", "ParentConnection",
    "OriginControlVector", "DestinationControlVector", "OriginConnectionIndex",
    "DestinationConnectionIndex", "Size",
}

# Only these three are reported: they are the units where "changed" is a question
# about behaviour. Everything else the range touched is in git_diff_commits.
UNIT_KINDS = {
    "Microflows$Microflow": "microflow",
    "Microflows$Nanoflow": "nanoflow",
    "Forms$Page": "page",
}


def _kind(label: str) -> str | None:
    for type_, kind in UNIT_KINDS.items():
        if type_ in label:
            return kind
    return None


def _name(label: str) -> str:
    return label.split(" (")[0] if " (" in label else label


def positional_only(differences: list) -> bool:
    """True when every difference is about where something sits on the canvas.

    False for an empty list on purpose: "nothing changed" is not "only the
    layout changed", and treating it as such would file untouched units under
    'moved'.
    """
    if not differences:
        return False
    return all(
        any(key in d.get("path", "") for key in POSITIONAL_KEYS)
        for d in differences
    )


def _group(units: list) -> dict:
    return {
        "microflows": [u["name"] for u in units if u["kind"] == "microflow"],
        "nanoflows": [u["name"] for u in units if u["kind"] == "nanoflow"],
        "pages": [u["name"] for u in units if u["kind"] == "page"],
    }


def git_units_between_commits(project: str = None, ref_a: str = "HEAD~1",
                              ref_b: str = "HEAD") -> dict:
    """Group the microflows, nanoflows and pages a range touched.

    'created' is new, 'moved' changed only on the canvas, 'changed' changed in
    substance. Refs work exactly as in git_diff_commits.
    """
    diff = collect_diffs(project, ref_a, ref_b)
    if not diff.get("success"):
        return diff

    created: list[dict] = []
    moved: list[dict] = []
    changed: list[dict] = []

    for entry in diff["files"]:
        kind = _kind(entry["label"])
        if kind is None:
            continue

        unit = {"name": _name(entry["label"]), "kind": kind}

        if entry["status"] == "created":
            created.append(unit)
        elif entry["status"] == "modified":
            if positional_only(entry.get("differences", [])):
                moved.append(unit)
            else:
                changed.append(unit)

    return {
        "success": True,
        "project": project,
        "repo": diff["repo"],
        "ref_a": diff["ref_a"],
        "ref_b": diff["ref_b"],
        "commits": diff["commits"],
        "totals": {
            "created": len(created),
            "moved": len(moved),
            "changed": len(changed),
        },
        "created": _group(created),
        "moved": _group(moved),
        "changed": _group(changed),
    }


TOOL_DEFINITION = {
    "name": "git_units_between_commits",
    "description": (
        "Return the microflows, nanoflows and pages created, moved and changed "
        "between two git refs.\n\n"
        "'created': new units. 'moved': the only differences are positional - "
        "somebody dragged things on the canvas and no behaviour changed. "
        "'changed': real changes to logic, actions, flows or properties.\n\n"
        "Use it to review a branch or a day's work without reading every "
        "property; refs work as in git_diff_commits."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": "Project key in projects.json. Defaults to the configured default.",
            },
            "ref_a": {
                "type": "string",
                "default": "HEAD~1",
                "description": "Base ref. For the last N commits use HEAD~N. Default: HEAD~1.",
            },
            "ref_b": {
                "type": "string",
                "default": "HEAD",
                "description": "New ref. Default: HEAD.",
            },
        },
        "required": [],
    },
}


if __name__ == "__main__":
    import pprint
    pprint.pprint(git_units_between_commits())
