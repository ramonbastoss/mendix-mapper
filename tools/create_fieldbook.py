"""Scaffold an empty fieldbook, optionally wiring it into projects.json.

A fieldbook is the project-specific half of what this engine reads: the things
about one app that its model cannot state. It is a repository of its own, with
one branch per branch of the Mendix app, so that knowledge written against one
model never gets read against another.

This creates the skeleton - manifest, knowledge directory, README - and stops.
It does not run git, does not commit, and does not create anything remote.
"""

import json
import os
import subprocess

from tools._utils import _CONFIG_PATH, resolve_project, resolve_repo_path

SCHEMA_VERSION = 1

_MANIFEST = """# Manifest of the {name} fieldbook.
#
# The engine reads this file first and discovers everything else from here. If
# it is missing, or declares a schema_version this engine does not support, the
# engine refuses the fieldbook instead of guessing its layout.

# Version of the CONTRACT, not of the content. The engine and each project's
# fieldbook move at their own pace; this line is what lets them drift apart
# loudly instead of silently.
schema_version: {schema_version}

# Short key. Matches the key used in the engine's projects.json.
project: {project}

# Human-readable name, used in logs and tool output.
name: "{name}"

# The branch of the Mendix app this fieldbook describes. REQUIRED.
#
# This repository carries one branch per branch of the app, and each clone keeps
# the matching one checked out. Before trusting the fieldbook, the engine
# compares this value with the working copy's branch and refuses when they
# differ - reading one branch's knowledge while working on another produces a
# wrong answer that looks right.
branch: {branch}

# Knowledge lives here: one markdown file per topic, never a single file, since
# a single file means a conflict on every merge request and nobody contributes
# twice. Each file needs the front-matter the engine requires; write_knowledge
# produces it for you.
knowledge: knowledge/

# Project-specific tools, listed explicitly rather than discovered: the engine
# EXECUTES this code on the machine of whoever uses it, so a new tool has to
# show up as a line in a merge request diff.
tools: []
"""

_README = """# {name} fieldbook

The project-specific knowledge that [mendix-mapper] reads when answering about
**{project}**.

The engine already knows how to read the `.mpr`, walk the dump, diff `.mxunit`
files and reason about the Mendix platform. What it cannot know is what only
someone who worked on this app knows - and that is what lives here.

## What belongs here

> Attributes, associations and access rules are already readable from the dump.
> **Only what the model cannot tell you belongs here.**

If the answer can be had by opening the dump, it does not go in this repository.
If it sounds like "this looks orphaned but is not", "these two fields look
interchangeable and are not", "the business uses a name that has no entity
behind it" - that is exactly what to write down.

## How to write an entry

Use the engine's `write_knowledge` tool rather than creating markdown by hand.
It fills in the front-matter, checks every entity name against the dump, and
refuses an entry whose branch does not match the working copy. Writing the same
title twice updates that entry instead of duplicating it.

## Version control is yours

The engine creates and edits this directory on disk. That is the whole of what
it does. It does not run `git init`, does not stage, does not commit, does not
push, and does not create or talk to any remote.

After a tool writes, the change sits unstaged in your working tree. Reviewing it,
committing it, branching and pushing are yours to do — a knowledge entry becomes
a commit under somebody's name, and deciding it is worth committing is not a
judgement the engine should make for you.

Which also means: **nothing here is backed up until you commit it.**

## One branch per branch of the app

This repository carries a branch for each branch of the Mendix app. Knowledge -
and especially any tool - written against one model may simply not hold against
another.

`branch:` in `fieldbook.yaml` declares which one this is, and the engine refuses
to answer when that does not match the working copy. It refuses — it does not fix
it: moving, merging or checking anything out is version control, and that is
yours.

The discipline this asks for: **when you merge code between branches of the app,
merge the fieldbook too.** Nothing automates that.

## What never goes in

- **Absolute machine paths.** Working copies differ per person; those belong in
  the engine's local `projects.json`, which is gitignored.
- **Tokens, passwords, credentials.** Not even as an example.
- **Mendix platform knowledge.** Anything true of any Mendix app belongs with
  the engine, not here.
- **Generated output.** What regenerates is not hand-edited.

[mendix-mapper]: https://github.com/ramonbastoss/mendix-mapper
"""


def _current_branch(repo: str) -> str:
    # stdin=DEVNULL is not optional. Under the stdio transport this process's
    # stdin is the JSON-RPC pipe the client is actively reading; a git that
    # inherits that handle never returns, and since it also holds the output
    # pipes open, the call hangs forever and takes the whole server with it.
    # The timeout is the second line of defence: an error beats a dead server.
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"git rev-parse timed out in '{repo}'. Pass 'branch' explicitly to "
            "scaffold without asking git."
        )
    if result.returncode != 0:
        raise RuntimeError(f"git rev-parse failed in '{repo}': {result.stderr.strip()}")
    return result.stdout.strip()


def _is_inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([
            os.path.abspath(child), os.path.abspath(parent)
        ]) == os.path.abspath(parent)
    except ValueError:  # different drives
        return False


def create_fieldbook(path: str, name: str = None, branch: str = None,
                     project: str = None, register: bool = False) -> dict:
    """Create an empty fieldbook at `path`.

    With `register=True`, point the given project's `fieldbook_path` at it, so
    the other tools find it without anyone editing configuration by hand.

    `branch` defaults to the branch the project's working copy is currently on,
    which is almost always the right answer and is the value the engine will
    later check against.
    """
    if not (path or "").strip():
        return {"success": False, "error": "path is required."}

    path = os.path.abspath(os.path.expanduser(path))
    key = None
    repo = None
    warnings = []

    # `project` omitted means the configured default, as everywhere else.
    # Registering only needs the config entry, so it is resolved on its own: a
    # working copy that has not been cloned yet is no reason to refuse writing a
    # path into projects.json.
    try:
        key, _ = resolve_project(project)
    except (FileNotFoundError, ValueError) as e:
        if register or project:
            return {"success": False, "error": str(e)}

    # The working copy is optional here, and only buys two things: deriving the
    # branch, and refusing a path inside the app's repository. Scaffolding an
    # empty fieldbook without one is fine - writing entries into it later is not,
    # since that compares the manifest's branch against the working copy.
    if key:
        try:
            repo = resolve_repo_path(project)
        except (FileNotFoundError, ValueError) as e:
            if not branch:
                return {
                    "success": False,
                    "error": (
                        f"{e} Pass 'branch' explicitly to scaffold without a "
                        "working copy - but note that write_knowledge will need "
                        "one, to check that branch before writing anything."
                    ),
                }
            warnings.append(
                f"{e} Scaffolded with the branch you gave, but write_knowledge "
                "will need that working copy before it writes anything."
            )

    if not branch and not repo:
        return {
            "success": False,
            "error": (
                "branch is required. Configure a project so it can be read from "
                "that working copy, or pass it explicitly."
            ),
        }

    # A fieldbook inside the Mendix repository is the one layout that was tried
    # and rejected: Studio Pro commits the whole working copy with no per-file
    # staging, so prose would be swept into model commits with no way to review
    # it separately.
    if repo and _is_inside(path, repo):
        return {
            "success": False,
            "error": (
                f"'{path}' is inside the Mendix working copy. A fieldbook has to "
                "live in its own repository: Studio Pro commits the whole working "
                "copy at once, so anything written there cannot be reviewed on "
                "its own. Put it somewhere outside the app's repository."
            ),
        }

    if os.path.isdir(path) and os.listdir(path):
        return {
            "success": False,
            "error": (
                f"'{path}' already exists and is not empty. Refusing to scaffold "
                "over it - point this at a new directory, or delete that one "
                "first if it is a failed attempt."
            ),
        }

    if not branch:
        try:
            branch = _current_branch(repo)
        except RuntimeError as e:
            return {"success": False, "error": str(e)}

    manifest_project = key or os.path.basename(path)
    display_name = name or manifest_project

    # ---- write the skeleton ---------------------------------------------
    knowledge_dir = os.path.join(path, "knowledge")
    os.makedirs(knowledge_dir, exist_ok=True)

    files = {
        os.path.join(path, "fieldbook.yaml"): _MANIFEST.format(
            schema_version=SCHEMA_VERSION,
            project=manifest_project,
            name=display_name,
            branch=branch,
        ),
        os.path.join(path, "README.md"): _README.format(
            name=display_name, project=manifest_project
        ),
        # git does not track empty directories, and an absent knowledge/ would
        # read as a broken fieldbook on a fresh clone.
        os.path.join(knowledge_dir, ".gitkeep"): "",
    }
    for file_path, content in files.items():
        with open(file_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    # ---- optionally wire it into projects.json ---------------------------
    registered = None
    if register:
        config_path = os.path.abspath(_CONFIG_PATH)
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)

        entry = config["projects"][key]
        previous = entry.get("fieldbook_path")
        if previous and os.path.abspath(previous) != path:
            warnings.append(
                f"Project '{key}' already pointed at '{previous}'; repointed it "
                "at the new fieldbook. The old one was left on disk untouched."
            )
        entry["fieldbook_path"] = path

        with open(config_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")
        registered = key

    return {
        "success": True,
        "path": path,
        "branch": branch,
        "created": sorted(os.path.relpath(p, path) for p in files),
        "registered_for_project": registered,
        "warnings": warnings,
        "next_step": (
            "The directory exists; nothing else was done. Version control is "
            f"yours: if you want it tracked, `git init` and a branch named "
            f"'{branch}' to match the manifest are the usual first steps. Add "
            "entries with write_knowledge."
        ),
    }


TOOL_DEFINITION = {
    "name": "create_fieldbook",
    "description": (
        "Scaffold an empty fieldbook - the project-specific knowledge a Mendix "
        "app's model cannot state - and optionally register it in projects.json "
        "so the other tools find it.\n\n"
        "Creates fieldbook.yaml, a knowledge/ directory and a README explaining "
        "what belongs in it. 'branch' defaults to the branch the project's "
        "working copy is currently on, which is the value the engine later "
        "checks against.\n\n"
        "Refuses to scaffold into a non-empty directory, or anywhere inside the "
        "Mendix working copy: Studio Pro commits the whole working copy at once, "
        "so a fieldbook living there could never be reviewed on its own.\n\n"
        "It only writes files. Version control is the user's: it does not run "
        "git init, stage, commit, push, or create or contact any remote. Say so "
        "when reporting success - a fieldbook that was never committed is not "
        "backed up by anything."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Directory to create the fieldbook in. Must be outside the "
                    "Mendix working copy."
                ),
            },
            "name": {
                "type": "string",
                "description": "Human-readable name. Defaults to the project key.",
            },
            "branch": {
                "type": "string",
                "description": (
                    "Branch of the Mendix app this fieldbook describes. Defaults "
                    "to the working copy's current branch."
                ),
            },
            "register": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Point the project's fieldbook_path at the new fieldbook, "
                    "preserving every other entry in projects.json."
                ),
            },
        },
        "required": ["path"],
    },
}


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "./fieldbook-example"
    print(json.dumps(create_fieldbook(target), indent=2, ensure_ascii=False))
