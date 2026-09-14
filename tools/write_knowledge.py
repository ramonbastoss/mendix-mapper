"""Write a knowledge entry into a fieldbook - or into the engine's own notes.

This is the only supported way to add knowledge. Hand-written markdown makes the
schema a convention, and a convention does not survive a second contributor:
the point of the contract is that the engine *refuses* what does not fit. Going
through a tool is what lets `entities` be checked against the dump at the moment
the entry is written, when the mistake is still cheap to fix.

It writes to the working tree and stops there. Reviewing the diff, committing and
pushing stay with whoever is writing - the engine never commits on someone's
behalf.
"""

import os
import re
import unicodedata
from datetime import date

from tools._utils import (current_branch, entity_index, resolve_project,
                          resolve_repo_path)

SUPPORTED_SCHEMA_VERSIONS = (1,)

VALID_TARGETS = ("fieldbook", "engine")

# Front-matter keys are English even when the body is not: the same parser reads
# the engine's own knowledge (published, English) and every project fieldbook, so
# the schema cannot be per-language.
REQUIRED_FRONT_MATTER = ("title", "entities", "modules", "updated")

# An absolute path is one person's machine leaking into a shared repo. Working
# copies differ per person; that belongs in the local projects.json.
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\[A-Za-z0-9_.-]+\\)")


def _slugify(title: str) -> str:
    """Deterministic file name from a title, so nobody invents a convention."""
    normalized = unicodedata.normalize("NFKD", title)
    ascii_only = "".join(c for c in normalized if not unicodedata.combining(c))
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return slug or "untitled"


def _yaml_list(values: list[str], indent: str = "  ") -> str:
    return "\n".join(f"{indent}- {v}" for v in values)


def _read_front_matter(text: str) -> dict:
    """Pull the front-matter block off an existing entry.

    Deliberately shallow: it only needs the scalars an update has to preserve
    (author, and the original title if none is given), not a general YAML tree.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end]
    found = {}
    for line in block.splitlines():
        match = re.match(r"^([a-z_]+):\s*(.*)$", line)
        if match and match.group(2).strip():
            found[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return found


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


def write_knowledge(
    title: str,
    body: str,
    entities: list[str] = None,
    modules: list[str] = None,
    author: str = None,
    target: str = "fieldbook",
    slug: str = None,
    project: str = None,
) -> dict:
    """Create or update one knowledge entry. Writes to disk, never commits.

    `target='fieldbook'` writes into the project's fieldbook and validates every
    name in `entities` against the dump. `target='engine'` writes platform
    knowledge into the engine's own knowledge/ directory and rejects `entities`
    outright - anything tied to a specific app's model is not platform knowledge.
    """
    if target not in VALID_TARGETS:
        return {
            "success": False,
            "error": f"Invalid target: '{target}'. Use one of {VALID_TARGETS}.",
        }
    if not (title or "").strip():
        return {"success": False, "error": "title is required."}
    if not (body or "").strip():
        return {"success": False, "error": "body is required."}

    entities = [e.strip() for e in (entities or []) if e.strip()]
    modules = [m.strip() for m in (modules or []) if m.strip()]

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

    warnings = []

    # ---- resolve where this entry goes ---------------------------------
    try:
        if target == "engine":
            if entities:
                return {
                    "success": False,
                    "error": (
                        "target='engine' does not accept `entities`. Engine "
                        "knowledge is about the Mendix platform and has to hold "
                        "without any particular app's model. If the note only "
                        "makes sense with those entities, it belongs in the "
                        "project fieldbook instead."
                    ),
                }
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

            # A fieldbook describes one branch of the Mendix app. Reading the Hmg
            # fieldbook while the working copy sits on Dsv produces a wrong answer
            # that looks right, so this refuses instead of warning.
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
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        return {"success": False, "error": str(e)}

    # ---- validate the entity names against the dump ---------------------
    if entities:
        try:
            index = entity_index(project)
        except (FileNotFoundError, ValueError) as e:
            return {"success": False, "error": str(e)}

        canonical, unknown = [], []
        for name in entities:
            match = index.get(name.lower())
            if match:
                canonical.append(match)
            else:
                unknown.append(name)

        if unknown:
            import difflib

            suggestions = {}
            for name in unknown:
                close = difflib.get_close_matches(name.lower(), index.keys(), n=3)
                if close:
                    suggestions[name] = [index[c] for c in close]
            return {
                "success": False,
                "error": (
                    "These names are not entities in the dump: "
                    f"{unknown}. `entities` is what links this entry back to the "
                    "model, so a name that does not resolve would quietly break "
                    "that link. Fix the name, or regenerate the dump if the "
                    "entity is newer than it."
                ),
                "did_you_mean": suggestions,
            }
        entities = sorted(set(canonical))

        derived = sorted({e.rsplit(".", 1)[0] for e in entities})
        if not modules:
            modules = derived
        else:
            missing = [m for m in derived if m not in modules]
            if missing:
                warnings.append(
                    f"modules did not list {missing}, which the entities live in; "
                    "added them."
                )
                modules = sorted(set(modules) | set(derived))

    # ---- write ----------------------------------------------------------
    file_slug = _slugify(slug or title)
    path = os.path.join(knowledge_dir, f"{file_slug}.md")
    existing = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
        previous = _read_front_matter(existing)
        if not author:
            author = previous.get("author")

    front = [
        "---",
        f"title: {title}",
        "entities:" if entities else "entities: []",
    ]
    if entities:
        front.append(_yaml_list(entities))
    front.append("modules:" if modules else "modules: []")
    if modules:
        front.append(_yaml_list(modules))
    front.append(f"updated: {date.today().isoformat()}")
    if author:
        front.append(f"author: {author}")
    front.append("---")

    content = "\n".join(front) + "\n\n" + body.strip() + "\n"

    os.makedirs(knowledge_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)

    return {
        "success": True,
        "action": "updated" if existing is not None else "created",
        "path": os.path.abspath(path),
        "target": target,
        "entities": entities,
        "modules": modules,
        "warnings": warnings,
        "next_step": (
            "Nothing was committed. Review the diff in the fieldbook repository "
            "and commit and push it yourself."
        ),
    }


TOOL_DEFINITION = {
    "name": "write_knowledge",
    "description": (
        "Create or update one knowledge entry, either in a project's fieldbook "
        "or in the engine's own knowledge directory. This is the supported way "
        "to add knowledge - it enforces the schema instead of trusting that "
        "hand-written markdown follows it.\n\n"
        "What it checks before writing: the fieldbook manifest exists and its "
        "schema_version is supported; the branch it declares matches the branch "
        "the working copy is on (refusing otherwise, since a fieldbook describes "
        "one branch); every name in `entities` resolves to a real entity in the "
        "dump, suggesting near misses when it does not; and the body carries no "
        "absolute machine path.\n\n"
        "Writing the same title twice updates that entry rather than creating a "
        "duplicate, preserving the recorded author unless a new one is given.\n\n"
        "It writes to the working tree and stops. Version control is the user's: "
        "it never stages, commits or pushes, and touches no remote. Say so when "
        "reporting success, so nobody assumes the entry is saved anywhere but "
        "their own disk.\n\n"
        "Use target='engine' only for knowledge that holds for any Mendix app; "
        "it rejects `entities` for that reason. Anything specific to one app "
        "goes in that project's fieldbook."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "One sentence naming the topic. Also the file name.",
            },
            "body": {
                "type": "string",
                "description": (
                    "Markdown body, without front-matter - the tool writes that. "
                    "Write what the model cannot tell you on its own: attributes "
                    "and associations are already readable from the dump."
                ),
            },
            "entities": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Qualified entity names ('Module.Entity') this entry is about. "
                    "This is the field that links knowledge back to code. Rejected "
                    "when target is 'engine'."
                ),
            },
            "modules": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Modules involved. Derived from entities if omitted.",
            },
            "author": {"type": "string"},
            "target": {
                "type": "string",
                "enum": list(VALID_TARGETS),
                "default": "fieldbook",
            },
            "slug": {
                "type": "string",
                "description": (
                    "Override the file name. Use it to update an entry whose title "
                    "you are rewording."
                ),
            },
        },
        "required": ["title", "body"],
    },
}


if __name__ == "__main__":
    import json

    print(json.dumps(
        write_knowledge(
            title="Example entry",
            body="Body of the entry.",
            target="engine",
        ),
        indent=2,
        ensure_ascii=False,
    ))
