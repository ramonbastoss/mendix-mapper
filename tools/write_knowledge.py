"""Write a markdown file into a fieldbook - or into the engine's own notes.

This is the only supported way to add knowledge. Not because of what it puts
in the file - it writes the body through, untouched - but because of what it
refuses before writing: a fieldbook with no manifest, a manifest declaring a
schema this engine does not support, a fieldbook whose branch does not match the
working copy, a path that climbs out of the knowledge directory, a body carrying
somebody's machine path. None of that depends on knowing the shape of an entry.

What the caller decides is the path, and the path is the whole of the layout:
`iteration-rule.md` lands at the root of the knowledge directory,
`IterationRules/iteration-rule.md` creates the subdirectory and files it
there. Grouping is therefore a convention between the people writing - the
engine neither imposes a taxonomy nor validates one.

It writes to the working tree and stops there. Reviewing the diff, committing and
pushing stay with whoever is writing - the engine never commits on someone's
behalf.
"""

import os
import re

from tools._utils import (current_branch, resolve_project, resolve_repo_path)

SUPPORTED_SCHEMA_VERSIONS = (1,)

VALID_TARGETS = ("fieldbook", "engine")

# Knowledge is markdown because markdown is what a merge request can review.
MARKDOWN_SUFFIX = ".md"

# An absolute path is one person's machine leaking into a shared repo. Working
# copies differ per person; that belongs in the local projects.json.
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\[A-Za-z0-9_.-]+\\)")


def _read_manifest(fieldbook_path: str) -> dict:
    """Read fieldbook.yaml. The manifest is the entry point; no manifest, no
    fieldbook - the engine refuses rather than guessing the layout."""
    manifest_path = os.path.join(fieldbook_path, "fieldbook.yaml")
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(
            f"No fieldbook.yaml at '{fieldbook_path}'. The manifest is the entry "
            "point and lives at the root of the fieldbook repository."
        )
    with open(manifest_path, encoding="utf-8") as f:
        text = f.read()

    manifest = {}
    for line in text.splitlines():
        match = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if match and match.group(2).strip():
            manifest[match.group(1)] = match.group(2).strip().strip('"').strip("'")

    if "schema_version" not in manifest:
        raise ValueError("fieldbook.yaml has no schema_version.")
    try:
        version = int(manifest["schema_version"])
    except ValueError:
        raise ValueError(
            f"schema_version '{manifest['schema_version']}' is not a number."
        )
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(
            f"fieldbook.yaml declares schema_version {version}; this engine "
            f"supports {list(SUPPORTED_SCHEMA_VERSIONS)}. Refusing rather than "
            "reading it with the wrong contract."
        )
    manifest["schema_version"] = version
    return manifest


def _resolve_entry_path(knowledge_dir: str, path: str) -> str:
    """Turn the caller's relative path into an absolute one inside knowledge_dir.

    The path is the only thing deciding layout now, so this is where the layout
    is defended: it has to stay inside the knowledge directory and it has to be
    markdown. Everything else about the shape of the tree is the caller's call.
    """
    raw = (path or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError(
            "path is required. It is relative to the fieldbook's knowledge "
            "directory - 'iteration-rule.md', or "
            "'IterationRules/iteration-rule.md' to group it in a subfolder."
        )

    if _ABSOLUTE_PATH.match(raw) or raw.startswith("/"):
        raise ValueError(
            f"path '{path}' is absolute. It has to be relative to the knowledge "
            "directory, which the fieldbook manifest declares - the engine, not "
            "the caller, decides where that directory lives."
        )

    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if not parts:
        raise ValueError(f"path '{path}' names no file.")
    if ".." in parts:
        raise ValueError(
            f"path '{path}' climbs out of the knowledge directory with '..'. "
            "Knowledge is written inside it or not at all."
        )

    name = parts[-1]
    stem, extension = os.path.splitext(name)
    if not stem:
        raise ValueError(f"path '{path}' has no file name, only an extension.")
    if not extension:
        parts[-1] = name + MARKDOWN_SUFFIX
    elif extension.lower() != MARKDOWN_SUFFIX:
        raise ValueError(
            f"path '{path}' ends in '{extension}'. Knowledge entries are "
            f"markdown ('{MARKDOWN_SUFFIX}'), because markdown is what a merge "
            "request can review line by line."
        )

    resolved = os.path.abspath(os.path.join(knowledge_dir, *parts))
    root = os.path.abspath(knowledge_dir)
    if os.path.commonpath([resolved, root]) != root:
        raise ValueError(
            f"path '{path}' resolves outside the knowledge directory."
        )
    return resolved


def write_knowledge(
    path: str,
    body: str,
    target: str = "fieldbook",
    project: str = None,
) -> dict:
    """Create or overwrite one markdown file. Writes to disk, never commits.

    `target='fieldbook'` writes into the project's fieldbook, after checking the
    manifest and the branch. `target='engine'` writes platform knowledge into the
    engine's own knowledge/ directory, which has no manifest and no branch to
    match - anything tied to one app's model does not belong there.

    The body is written through exactly as given: no front-matter, no header, no
    normalisation. What the file says is entirely the caller's.
    """
    if target not in VALID_TARGETS:
        return {
            "success": False,
            "error": f"Invalid target: '{target}'. Use one of {VALID_TARGETS}.",
        }
    if not (body or "").strip():
        return {"success": False, "error": "body is required."}

    leak = _ABSOLUTE_PATH.search(body)
    if leak:
        return {
            "success": False,
            "error": (
                f"The body contains what looks like an absolute path "
                f"('{leak.group(0)}...'). Machine paths differ per person and do "
                "not belong in a shared repository - they live in the local "
                "projects.json, which is gitignored."
            ),
        }

    # ---- resolve where this file goes -----------------------------------
    try:
        if target == "engine":
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            knowledge_dir = os.path.join(root, "knowledge")
        else:
            key, proj = resolve_project(project)
            fieldbook_path = proj.get("fieldbook_path")
            if not fieldbook_path:
                return {
                    "success": False,
                    "error": (
                        f"Project '{key}' has no 'fieldbook_path' in "
                        "projects.json. Point it at your clone of the project's "
                        "fieldbook repository."
                    ),
                }
            if not os.path.isdir(fieldbook_path):
                return {
                    "success": False,
                    "error": f"fieldbook_path '{fieldbook_path}' does not exist.",
                }

            manifest = _read_manifest(fieldbook_path)

            # A fieldbook describes one branch of the Mendix app. Reading the
            # release fieldbook while the working copy sits on main produces a
            # wrong answer that looks right, so this refuses instead of warning.
            declared = manifest.get("branch")
            if not declared:
                return {
                    "success": False,
                    "error": (
                        "fieldbook.yaml has no 'branch'. It is required: the "
                        "engine compares it with the working copy's branch before "
                        "trusting the fieldbook."
                    ),
                }
            working_copy_branch = current_branch(resolve_repo_path(project))
            if declared != working_copy_branch:
                return {
                    "success": False,
                    "error": (
                        f"This fieldbook declares branch '{declared}' but the "
                        f"working copy is on '{working_copy_branch}'. Refusing to "
                        "write: check out the matching fieldbook branch first."
                    ),
                }

            knowledge_dir = os.path.join(
                fieldbook_path, manifest.get("knowledge", "knowledge/")
            )

        entry_path = _resolve_entry_path(knowledge_dir, path)
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        return {"success": False, "error": str(e)}

    # ---- write ----------------------------------------------------------
    existed = os.path.exists(entry_path)

    warnings = []
    parent = os.path.dirname(entry_path)
    created_directory = not os.path.isdir(parent)

    os.makedirs(parent, exist_ok=True)
    with open(entry_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(body.strip() + "\n")

    if created_directory:
        # A typo in a folder name is otherwise invisible: it just quietly grows a
        # sibling directory next to the one that was meant.
        warnings.append(
            f"Created directory '{os.path.relpath(parent, knowledge_dir)}'. If "
            "the entry was meant to join an existing group, check the spelling."
        )

    return {
        "success": True,
        "action": "overwritten" if existed else "created",
        "path": os.path.abspath(entry_path),
        "relative_path": os.path.relpath(entry_path, knowledge_dir).replace(
            "\\", "/"
        ),
        "target": target,
        "warnings": warnings,
        "next_step": (
            "Nothing was committed. Review the diff in the fieldbook repository "
            "and commit and push it yourself."
        ),
    }


TOOL_DEFINITION = {
    "name": "write_knowledge",
    "description": (
        "Create or overwrite one markdown file of knowledge, either in a "
        "project's fieldbook or in the engine's own knowledge directory. This is "
        "the supported way to add knowledge: not because it formats the entry - "
        "the body is written through untouched - but because of what it refuses "
        "first.\n\n"
        "What it checks before writing: the fieldbook manifest exists and its "
        "schema_version is supported; the branch it declares matches the branch "
        "the working copy is on, refusing otherwise, since a fieldbook describes "
        "one branch; the path stays inside the knowledge directory and names a "
        "markdown file; and the body carries no absolute machine path.\n\n"
        "`path` is relative to the knowledge directory and is the whole of the "
        "layout: 'iteration-rule.md' files it at the root, "
        "'IterationRules/iteration-rule.md' creates that subfolder and files "
        "it inside. Grouping is a convention between the people writing - no "
        "taxonomy is imposed or validated, so reuse a folder that already exists "
        "rather than inventing a near-duplicate of it.\n\n"
        "Writing the same path twice OVERWRITES that file; there is no merge and "
        "no dedup by content, so read what is there before replacing it.\n\n"
        "It writes to the working tree and stops. Version control is the user's: "
        "it never stages, commits or pushes, and touches no remote. Say so when "
        "reporting success, so nobody assumes the entry is saved anywhere but "
        "their own disk.\n\n"
        "Use target='engine' only for knowledge that holds for any Mendix app. "
        "Anything specific to one app goes in that project's fieldbook."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Where to write, relative to the knowledge directory. "
                    "'iteration-rule.md' or 'Subfolder/iteration-rule.md'. "
                    "Missing '.md' is appended; any other extension is refused."
                ),
            },
            "body": {
                "type": "string",
                "description": (
                    "The full markdown content of the file, written through as "
                    "given. Write what the model cannot tell you on its own: "
                    "attributes and associations are already readable from the "
                    "dump."
                ),
            },
            "target": {
                "type": "string",
                "enum": list(VALID_TARGETS),
                "default": "fieldbook",
            },
        },
        "required": ["path", "body"],
    },
}


if __name__ == "__main__":
    import json

    print(json.dumps(
        write_knowledge(
            path="a-note.md",
            body="Body of the entry.",
            target="engine",
        ),
        indent=2,
        ensure_ascii=False,
    ))
