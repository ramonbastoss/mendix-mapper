"""The history of one unit: who touched it, when, and whether it mattered.

Answering "who changed this microflow" by diffing commit ranges means decoding
everything in them. Going the other way - resolve the unit to its file, ask git
for that file's history - is a different order of magnitude, and it is what this
does.
"""

import re

from tools._utils import git_text, load_units, resolve_repo_path, unit_names
from tools.diff_mxunit import decode_unit, diff_documents
from tools.git_diff_commits import read_blob
from tools.git_units_between_commits import positional_only

_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_FORMAT = "%H\t%an\t%ae\t%ai\t%s"


def unit_file(unit_id: str) -> str:
    """Where a unit's `.mxunit` lives, derived from its $ID.

    Mendix shards mprcontents/ by the first two pairs of characters of the id,
    so no lookup is needed to go from a unit to its file.
    """
    unit_id = unit_id.lower()
    return f"mprcontents/{unit_id[0:2]}/{unit_id[2:4]}/{unit_id}.mxunit"


def _resolve_unit(unit: str, project: str = None) -> dict:
    """Turn an $ID, a qualified name or a plain name into one unit's id."""
    if _UUID.match(unit):
        return {"success": True, "id": unit.lower(), "label": unit}

    units = load_units(project)
    wanted = unit.lower()
    matches = [
        u for u in units
        if u.get("$ID") and any(n.lower() == wanted for n in unit_names(u))
    ]

    if not matches:
        return {"success": False, "error": f"Unit '{unit}' is not in the dump."}

    if len(matches) > 1:
        return {
            "success": False,
            "error": (
                f"'{unit}' is ambiguous - {len(matches)} units carry that name. "
                "Use the qualified name ('Module.Name') or the $ID."
            ),
            # Qualified name when there is one, plain name otherwise: a folder
            # carries no $QualifiedName, and a candidate list whose names are all
            # null is no help in choosing.
            "candidates": [
                {"id": u["$ID"],
                 "name": u.get("$QualifiedName") or u.get("name"),
                 "type": u.get("$Type")}
                for u in matches
            ],
        }

    found = matches[0]
    return {
        "success": True,
        "id": found["$ID"].lower(),
        "label": found.get("$QualifiedName") or found.get("name") or found["$ID"],
        "type": found.get("$Type"),
    }


def _classify(repo: str, commit: str, path: str) -> tuple[str, int]:
    """What that commit did to the unit, by comparing it with its parent."""
    current = read_blob(repo, commit, path)
    parent = read_blob(repo, f"{commit}^", path)

    if current is None:
        return ("deleted", 0)
    if parent is None:
        return ("created", 0)

    doc_now = decode_unit(current)
    doc_before = decode_unit(parent)
    if doc_now is None or doc_before is None:
        return ("bson_error", 0)

    differences = diff_documents(doc_before, doc_now)
    if not differences:
        return ("no_diff", 0)
    if positional_only(differences):
        return ("moved", len(differences))
    return ("changed", len(differences))


def git_unit_history(
    unit: str,
    project: str = None,
    author: str = None,
    since: str = None,
    until: str = None,
    branch: str = None,
    limit: int = None,
    include_merges: bool = False,
    classify: bool = True,
) -> dict:
    """Commits that touched one unit, newest first.

    `classify` decodes the unit at each commit and says whether the change was
    real or only positional. It costs two git reads and a decode per commit, so
    turn it off when the list itself is the answer.
    """
    try:
        repo = resolve_repo_path(project)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}

    try:
        resolved = _resolve_unit(unit, project)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}
    if not resolved["success"]:
        return resolved

    path = unit_file(resolved["id"])

    # --follow keeps the history across renames, which in Mendix means the unit
    # moved between folders or modules.
    args = ["log", "--follow", f"--pretty=format:{_FORMAT}"]
    if not include_merges:
        args.append("--no-merges")
    if branch:
        args.append(branch)
    if author:
        args.append(f"--author={author}")
    if since:
        args.append(f"--after={since}")
    if until:
        args.append(f"--before={until}")
    if limit:
        args += ["-n", str(limit)]
    args += ["--", path]

    try:
        output = git_text(repo, *args)
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    commits = []
    for line in output.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 4)
        if len(parts) < 5:
            continue
        full_hash, name, email, date, message = parts
        commit = {
            "hash": full_hash[:7],
            "full_hash": full_hash,
            "author_name": name,
            "author_email": email,
            "date": date,
            "message": message,
        }
        if classify:
            change_type, total = _classify(repo, full_hash, path)
            commit["change_type"] = change_type
            commit["total_differences"] = total
        commits.append(commit)

    return {
        "success": True,
        "project": project,
        "repo": repo,
        "unit": {
            "id": resolved["id"],
            "label": resolved.get("label"),
            "type": resolved.get("type"),
            "file": path,
        },
        "filters": {
            "author": author,
            "since": since,
            "until": until,
            "branch": branch,
            "limit": limit,
            "include_merges": include_merges,
            "classify": classify,
        },
        "total_commits": len(commits),
        "commits": commits,
    }


TOOL_DEFINITION = {
    "name": "git_unit_history",
    "description": (
        "Return the commits that changed one unit - a microflow, nanoflow, page, "
        "domain model, security unit - newest first. This is the tool for 'who "
        "changed X', 'when was X last touched', 'history of X'.\n\n"
        "Identify the unit by $ID (UUID), qualified name ('Module.Name') or plain "
        "name; an ambiguous plain name comes back with the candidates.\n\n"
        "With classify=true (the default) each commit also says what kind of "
        "change it was: created, changed (logic), moved (only dragged on the "
        "canvas), no_diff, or deleted. Far faster and more exact than bisecting "
        "with git_units_between_commits."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "unit": {
                "type": "string",
                "description": (
                    "$ID (UUID), qualified name ('Module.Name') or plain name. "
                    "A plain name matching several units returns an error listing "
                    "the candidates."
                ),
            },
            "project": {
                "type": "string",
                "description": (
                    "Project key in projects.json - used both to find the git "
                    "repository and to resolve the name against the dump. "
                    "Defaults to the configured default."
                ),
            },
            "author": {
                "type": "string",
                "description": "Author name or e-mail, partial and case-insensitive.",
            },
            "since": {
                "type": "string",
                "description": "Start date. Any git format: '2024-01-01', '2 weeks ago'.",
            },
            "until": {"type": "string", "description": "End date, same formats."},
            "branch": {
                "type": "string",
                "description": "A specific branch. Defaults to the one checked out.",
            },
            "limit": {"type": "integer", "description": "Maximum number of commits."},
            "include_merges": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Include merge commits. Off by default: a merge shows up as "
                    "touching everything the other branch touched, which buries "
                    "the commit that actually made the change."
                ),
            },
            "classify": {
                "type": "boolean",
                "default": True,
                "description": (
                    "Decode the unit at each commit to tell a real change from a "
                    "positional one. Turn it off for a faster answer when the list "
                    "of commits is all you need."
                ),
            },
        },
        "required": ["unit"],
    },
}


if __name__ == "__main__":
    import pprint
    pprint.pprint(git_unit_history("Administration.Account_Overview", limit=3))
