"""Semantic diff of two `.mxunit` documents.

A Mendix app is stored as one BSON file per unit, which is why `git diff` on a
Mendix repository says nothing useful: every change looks like a binary blob
being replaced. Decode both sides and the actual change becomes readable - this
property went from X to Y, this element was added, this one is gone.

This module is the engine, not a tool: nothing registers it with the server.
The question people actually ask is "what changed between these commits", and
`git_diff_commits` answers it by pulling both blobs out of git and handing them
to `diff_documents` below.

`bson` comes from **pymongo**. The PyPI package named `bson` is an unrelated
project and will not work here - see requirements.txt.
"""

from typing import Any

import bson


def to_serializable(obj: Any) -> Any:
    """Make a decoded BSON document safe to put in a JSON response.

    BSON carries raw `bytes` (Mendix stores identifiers and blobs that way) and
    json.dumps refuses those, so they become hex strings.
    """
    if isinstance(obj, dict):
        return {k: to_serializable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_serializable(i) for i in obj]
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


def decode_unit(data: bytes) -> dict | None:
    """Decode a `.mxunit` blob, or None if it is not readable BSON.

    None rather than an exception: a single unreadable unit in a commit that
    touched three hundred of them should be reported as one odd file, not abort
    the whole comparison.
    """
    try:
        return to_serializable(bson.decode(data))
    except Exception:
        return None


def load_unit(path: str) -> dict:
    """Decode a `.mxunit` file from disk."""
    with open(path, "rb") as f:
        return to_serializable(bson.decode(f.read()))


def diff_documents(a: Any, b: Any, path: str = "") -> list[dict]:
    """Every difference between two decoded units, deepest level included.

    Each difference is {"path", "kind"} plus either "from"/"to" (changed) or
    "value" (added, removed). `path` is a dotted trail into the document, which
    is what lets a caller tell a logic change from a cosmetic one without
    understanding the model: keys like Location and Size are where Studio Pro
    records that something was dragged on the canvas.
    """
    results = []

    if type(a) is not type(b):
        return [{"path": path, "kind": "changed", "from": a, "to": b}]

    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            child = f"{path}.{key}" if path else key
            if key not in a:
                results.append({"path": child, "kind": "added", "value": b[key]})
            elif key not in b:
                results.append({"path": child, "kind": "removed", "value": a[key]})
            else:
                results.extend(diff_documents(a[key], b[key], child))

    elif isinstance(a, list):
        # Pair list elements by $ID when they have one. Comparing by position
        # would report a reordered list as if everything in it had changed, and
        # Studio Pro reorders freely: the same microflow action can move around
        # the array without anyone touching it.
        a_by_id = {i["$ID"]: i for i in a if isinstance(i, dict) and "$ID" in i}
        b_by_id = {i["$ID"]: i for i in b if isinstance(i, dict) and "$ID" in i}

        if a_by_id or b_by_id:
            for id_ in sorted(set(a_by_id) | set(b_by_id)):
                child = f"{path}[{id_}]"
                if id_ not in a_by_id:
                    results.append({"path": child, "kind": "added", "value": b_by_id[id_]})
                elif id_ not in b_by_id:
                    results.append({"path": child, "kind": "removed", "value": a_by_id[id_]})
                else:
                    results.extend(diff_documents(a_by_id[id_], b_by_id[id_], child))
        else:
            for idx, (ai, bi) in enumerate(zip(a, b)):
                results.extend(diff_documents(ai, bi, f"{path}[{idx}]"))
            for idx in range(len(b), len(a)):
                results.append({"path": f"{path}[{idx}]", "kind": "removed", "value": a[idx]})
            for idx in range(len(a), len(b)):
                results.append({"path": f"{path}[{idx}]", "kind": "added", "value": b[idx]})

    elif a != b:
        results.append({"path": path, "kind": "changed", "from": a, "to": b})

    return results


def unit_label(doc: dict) -> str:
    """A human-readable name for a decoded unit, for listings."""
    name = doc.get("Name") or doc.get("name") or doc.get("$QualifiedName") or ""
    type_ = doc.get("$Type") or ""
    if name and type_:
        return f"{name} ({type_})"
    return name or type_ or "(unnamed)"
