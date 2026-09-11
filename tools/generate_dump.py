"""Regenerate a project's dump from its .mpr file."""

import os
import subprocess

from tools._utils import resolve_project


def generate_dump(project: str = None) -> dict:
    """Regenerate the dump for a project by running `mx dump-mpr`.

    The dump is derived data: it is a snapshot of the .mpr at the moment it ran,
    and it does not update itself when the working copy changes.
    """
    try:
        key, proj = resolve_project(project)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}

    mpr_path = proj.get("mpr_path")
    dump_path = proj.get("dump_path")

    if not mpr_path:
        return {"success": False,
                "error": f"'mpr_path' is not set for project '{key}' in projects.json"}

    if not dump_path:
        return {"success": False,
                "error": f"'dump_path' is not set for project '{key}' in projects.json"}

    if not os.path.exists(mpr_path):
        return {"success": False, "error": f".mpr file not found: {mpr_path}"}

    os.makedirs(os.path.dirname(dump_path), exist_ok=True)

    try:
        # Falls back to whatever `mx` is on PATH. On Windows it usually is not,
        # which is why mx_path is configured per project - different apps can
        # sit on different Studio Pro versions.
        mx_path = proj.get("mx_path", "mx")

        with open(dump_path, "w", encoding="utf-8") as dump_file:
            result = subprocess.run(
                [mx_path, "dump-mpr", mpr_path],
                stdin=subprocess.DEVNULL,
                stdout=dump_file,
                stderr=subprocess.PIPE,
                text=True,
            )

        if result.returncode != 0:
            return {"success": False,
                    "error": f"mx dump-mpr failed: {result.stderr}"}

        size_mb = os.path.getsize(dump_path) / (1024 * 1024)

        return {
            "success": True,
            "project": key,
            "message": "Dump generated.",
            "path": dump_path,
            "size_mb": round(size_mb, 2),
        }

    except FileNotFoundError:
        return {
            "success": False,
            "error": (
                f"'{proj.get('mx_path', 'mx')}' not found. Set 'mx_path' in "
                "projects.json to the mx.exe of your Studio Pro install."
            ),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


TOOL_DEFINITION = {
    "name": "generate_dump",
    "description": (
        "Regenerate the JSON dump of a Mendix app from its .mpr file. "
        "Call this when the working copy has changed and the answers should "
        "reflect the latest model."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": "Project key from projects.json. Defaults to the configured default.",
            },
        },
        "required": [],
    },
}


if __name__ == "__main__":
    print(generate_dump())
