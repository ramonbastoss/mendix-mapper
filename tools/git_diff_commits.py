"""What changed between two git refs, read as model changes instead of blobs.

This is the tool the whole BSON decoding exists for: point it at two commits and
get back which units were created, modified or deleted, and - for the modified
ones - exactly which properties moved and from what to what.
"""

import json
import os
from datetime import datetime

from tools._utils import git_bytes, git_text, resolve_repo_path
from tools.diff_mxunit import decode_unit, diff_documents, unit_label

_DIFFS_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "diffs")


def _resolve_ref(repo: str, ref: str) -> str:
    return git_text(repo, "rev-parse", "--short", ref).strip()


def _commits_between(repo: str, ref_a: str, ref_b: str) -> list[dict]:
    """Commits from ref_a (exclusive) to ref_b (inclusive), oldest first."""
    log = git_text(repo, "log", "--pretty=format:%H\t%s", f"{ref_a}..{ref_b}")
    commits = []
    for line in log.strip().splitlines():
        if "\t" in line:
            full_hash, message = line.split("\t", 1)
            commits.append({"hash": full_hash, "message": message})
    return list(reversed(commits))


def _changed_units(repo: str, ref_a: str, ref_b: str) -> list[str]:
    output = git_text(repo, "diff", "--name-only", ref_a, ref_b)
    return [p for p in output.strip().splitlines() if p.endswith(".mxunit")]


def read_blob(repo: str, ref: str, path: str) -> bytes | None:
    """The file's contents at that ref, or None if it did not exist there.

    Shared with git_unit_history: "did this file exist in that commit" is the
    same question there, and git answers it by failing.
    """
    try:
        return git_bytes(repo, "show", f"{ref}:{path}")
    except RuntimeError:
        return None


def collect_diffs(project: str = None, ref_a: str = "HEAD~1",
                  ref_b: str = "HEAD") -> dict:
    """Every difference between two refs, in full.

    Internal: this is what `git_diff_commits` summarizes and what
    `git_units_between_commits` classifies. It is deliberately not a tool -
    a large range produces megabytes of JSON, which is exactly what the MCP
    transport cannot carry.
    """
    try:
        repo = resolve_repo_path(project)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}

    try:
        sha_a = _resolve_ref(repo, ref_a)
        sha_b = _resolve_ref(repo, ref_b)
        commits = _commits_between(repo, ref_a, ref_b)
        paths = _changed_units(repo, ref_a, ref_b)
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    files = []
    for path in paths:
        blob_a = read_blob(repo, ref_a, path)
        blob_b = read_blob(repo, ref_b, path)

        if blob_a is None and blob_b is None:
            continue

        if blob_a is None:
            doc = decode_unit(blob_b)
            files.append({
                "file": path,
                "label": unit_label(doc) if doc else path,
                "status": "created",
                "total_differences": 0,
                "differences": [],
            })
            continue

        if blob_b is None:
            doc = decode_unit(blob_a)
            files.append({
                "file": path,
                "label": unit_label(doc) if doc else path,
                "status": "deleted",
                "total_differences": 0,
                "differences": [],
            })
            continue

        doc_a = decode_unit(blob_a)
        doc_b = decode_unit(blob_b)

        if doc_a is None or doc_b is None:
            files.append({
                "file": path,
                "label": path,
                "status": "bson_error",
                "total_differences": 0,
                "differences": [],
            })
            continue

        differences = diff_documents(doc_a, doc_b)
        files.append({
            "file": path,
            "label": unit_label(doc_b),
            "status": "modified",
            "total_differences": len(differences),
            "differences": differences,
        })

    return {
        "success": True,
        "project": project,
        "repo": repo,
        "ref_a": sha_a,
        "ref_b": sha_b,
        "commits": commits,
        "total_files": len(files),
        "files": files,
    }


def git_diff_commits(project: str = None, ref_a: str = "HEAD~1",
                     ref_b: str = "HEAD") -> dict:
    """Compare two refs and report which units changed.

    The full comparison is written to temp/diffs/ and the response carries a
    summary plus the path to it. A single commit in a real app can touch
    hundreds of units and produce megabytes of differences, which would drop the
    transport; reading the file with the Read tool costs one extra step and
    always works.
    """
    full = collect_diffs(project, ref_a, ref_b)
    if not full.get("success"):
        return full

    os.makedirs(_DIFFS_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.abspath(os.path.join(
        _DIFFS_DIR, f"diff_{full['ref_a']}_{full['ref_b']}_{stamp}.json"
    ))
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(full, f, indent=2, ensure_ascii=False)

    summary = [
        {k: v for k, v in entry.items() if k != "differences"}
        for entry in full["files"]
    ]
    total_differences = sum(e["total_differences"] for e in full["files"])

    return {
        "success": True,
        "project": project,
        "repo": full["repo"],
        "ref_a": full["ref_a"],
        "ref_b": full["ref_b"],
        "commits": full["commits"],
        "total_files": full["total_files"],
        "total_differences": total_differences,
        "files": summary,
        "output_file": output_file,
        "note": (
            "Property-level differences are in output_file, under each file's "
            "'differences'. Read it with the Read tool."
        ),
    }


TOOL_DEFINITION = {
    "name": "git_diff_commits",
    "description": (
        "Compare two git refs (commits, branches, tags) and report what changed "
        "in the Mendix model - not in the bytes. Answers 'what changed in the "
        "last 3 commits', 'what changed between X and Y', 'what does this branch "
        "change'.\n\n"
        "Returns one entry per .mxunit touched, with its status (created, "
        "modified, deleted) and how many properties differ. The property-level "
        "detail is written to temp/diffs/ and pointed at by 'output_file', "
        "because one commit can touch hundreds of units.\n\n"
        "For 'the last N commits' use ref_a='HEAD~N'."
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
                "description": "Base ref: commit hash, branch or tag. Default: HEAD~1.",
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
    pprint.pprint(git_diff_commits())
