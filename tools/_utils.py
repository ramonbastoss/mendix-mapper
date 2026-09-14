"""Shared helpers: configuration, dump loading, git, and response size capping."""

import json
import os
import subprocess

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "projects.json")

# The Claude Code MCP client drops the transport when a single response goes
# past ~16 MB on stdout. A unit's JSON gets escaped on its way into the JSON-RPC
# envelope (quotes and backslashes double up), so it can inflate roughly 3x.
# A 2 MB cap per unit leaves comfortable headroom even when several large units
# land in the same response.
UNIT_LIMIT_MB = 2.0

_SPILL_DIR = os.path.join(os.path.dirname(__file__), "..", "temp", "units")

_NO_CONFIG = (
    "projects.json not found. Copy projects.example.json to projects.json and "
    "point it at your Mendix working copy. It is gitignored on purpose: every "
    "path in it is specific to your machine."
)


def _load_config() -> dict:
    if not os.path.exists(_CONFIG_PATH):
        raise FileNotFoundError(_NO_CONFIG)
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def resolve_project(project: str = None) -> tuple[str, dict]:
    """Return (key, project_config) from projects.json.

    Each entry is one working copy, not one app: if you keep a checkout per
    branch, each branch is its own entry with its own dump.
    """
    config = _load_config()
    key = project or config.get("default")
    if not key:
        raise ValueError(
            "No project given and no 'default' set in projects.json."
        )
    projects = config.get("projects", {})
    if key not in projects:
        available = list(projects.keys())
        raise ValueError(f"Project '{key}' not found. Available: {available}")
    return key, projects[key]


def load_units(project: str = None) -> list:
    """Load the `units` array from the project's dump."""
    _, proj = resolve_project(project)
    dump_path = proj["dump_path"]
    if not os.path.exists(dump_path):
        raise FileNotFoundError(
            f"Dump not found at {dump_path}. Run generate_dump first."
        )
    with open(dump_path, encoding="utf-8") as f:
        return json.load(f)["units"]


def resolve_repo_path(project: str = None) -> str:
    """Resolve a project key to the absolute path of its git repository.

    Derived from the parent directory of the configured `mpr_path`, so the repo
    never needs a config entry of its own. Validates that the directory exists
    and contains a .git.
    """
    key, proj = resolve_project(project)
    mpr_path = proj.get("mpr_path")
    if not mpr_path:
        raise ValueError(f"Project '{key}' has no 'mpr_path' in projects.json.")
    repo = os.path.dirname(mpr_path)
    if not os.path.isdir(repo):
        raise FileNotFoundError(
            f"Repository directory '{repo}' does not exist (project '{key}')."
        )
    if not os.path.isdir(os.path.join(repo, ".git")):
        raise FileNotFoundError(
            f"Directory '{repo}' is not a git repository (project '{key}')."
        )
    return repo


GIT_TIMEOUT = 120


def git_bytes(repo: str, *args) -> bytes:
    """Run git inside `repo` and return its stdout, raising on failure.

    Every git call in this server goes through here, for two reasons that are
    easy to get wrong once each:

    `stdin=DEVNULL` is not optional. Under the stdio transport this process's
    stdin is the JSON-RPC pipe the client is actively reading; a git that
    inherits that handle never returns, and since it also holds the output pipes
    open, the call hangs forever and takes the whole server down with it - not
    just that one tool.

    `--no-pager` for the same family of reason: a pager waiting for a terminal
    that is not there is another way to hang.

    The timeout is the last line of defence. An error beats a dead server.
    """
    try:
        result = subprocess.run(
            ["git", "--no-pager", *args],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"`git {' '.join(args)}` timed out after {GIT_TIMEOUT}s in '{repo}'."
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def git_text(repo: str, *args) -> str:
    """`git_bytes`, decoded. Use it for anything but blob contents."""
    return git_bytes(repo, *args).decode(errors="replace")


def current_branch(repo: str) -> str:
    """The branch the working copy is on."""
    return git_text(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()


def entity_index(project: str = None) -> dict[str, str]:
    """Map every domain entity of the app to its qualified name.

    Returns {lowercased "Module.Entity": "Module.Entity"}, so a caller can do a
    case-insensitive lookup and still report the canonical casing back.

    Entities are NOT top-level units: they live inside a
    `DomainModels$DomainModel` unit, which itself carries no name and no
    $QualifiedName - only a `$ContainerID` pointing at its `Projects$Module`.
    That is why searching the units array for an entity name finds nothing, and
    why the qualified name has to be assembled here instead of read off a field.
    """
    units = load_units(project)

    module_by_id = {
        u["$ID"]: u.get("name")
        for u in units
        if u.get("$Type") == "Projects$Module" and u.get("$ID")
    }

    index = {}
    for u in units:
        if u.get("$Type") != "DomainModels$DomainModel":
            continue
        module = module_by_id.get(u.get("$ContainerID"))
        if not module:
            continue
        for entity in u.get("entities") or []:
            name = entity.get("name")
            if not name:
                continue
            qualified = f"{module}.{name}"
            index[qualified.lower()] = qualified

    return index


def unit_names(unit: dict) -> list[str]:
    names = []
    if unit.get("$QualifiedName"):
        names.append(unit["$QualifiedName"])
    if unit.get("name"):
        names.append(unit["name"])
    return names


def cap_unit(unit: dict, limit_mb: float = UNIT_LIMIT_MB) -> dict:
    """Keep a single unit within the client's stdout limit.

    If the serialized unit exceeds `limit_mb`, write it to temp/units/<id>.json
    and return a stub carrying its metadata plus `spill=True` and `output_file`.
    Otherwise return the unit untouched.
    """
    if not isinstance(unit, dict):
        return unit

    serialized = json.dumps(unit, ensure_ascii=False)
    size_bytes = len(serialized.encode("utf-8"))
    if size_bytes <= limit_mb * 1024 * 1024:
        return unit

    os.makedirs(_SPILL_DIR, exist_ok=True)
    uid = unit.get("$ID") or "no-id"
    path = os.path.abspath(os.path.join(_SPILL_DIR, f"{uid}.json"))
    with open(path, "w", encoding="utf-8") as f:
        f.write(serialized)

    return {
        "$ID": unit.get("$ID"),
        "$Type": unit.get("$Type"),
        "$QualifiedName": unit.get("$QualifiedName"),
        "name": unit.get("name"),
        "spill": True,
        "output_file": path,
        "size_mb": round(size_bytes / 1024 / 1024, 2),
        "warning": (
            f"Unit exceeds {limit_mb} MB - full JSON written to '{path}'. "
            "Use the Read tool (with offset/limit if needed) to inspect it."
        ),
    }


def cap_units(units: list, limit_mb: float = UNIT_LIMIT_MB) -> list:
    """Apply `cap_unit` to every element of a list of units."""
    return [cap_unit(u, limit_mb) for u in units]
