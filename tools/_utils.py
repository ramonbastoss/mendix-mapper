"""Shared helpers: configuration, dump loading, git, and response size capping."""

import json
import os
import subprocess
import threading
import time

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


# --- dump cache ------------------------------------------------------------
#
# Parsing the dump is the dominant cost of nearly every tool. A large app's dump
# runs to a few hundred MB and some thousands of units, which is ~1.6s to read
# and parse, and without a cache every single call pays it again: a burst of 200
# reads was measured at 330s, almost all of it the same bytes re-parsed.
#
# Two things end the cache's life, and nothing else:
#
#   1. a different dump - the key is (path, mtime), so writing a fresh dump
#      invalidates it as a side effect, with no coupling to generate_dump;
#   2. disuse - the sweeper thread drops it after _CACHE_TTL_SECONDS without a
#      read. That matters because the server runs as one process per editor
#      session: several idle sessions each pinning a parsed dump costs far more
#      than re-reading the file would.
#
# Only ONE dump is held - the last one asked for. Keying the cache by project
# instead would let a single process accumulate every working copy you query.

_CACHE_TTL_SECONDS = 20.0
_CACHE_SWEEP_SECONDS = 5.0

_cache_lock = threading.Lock()
_cache_key = None       # (absolute dump path, mtime_ns) the units came from
_cache_units = None     # the parsed `units` array, or None when the cache is empty
_cache_read_at = 0.0    # time.monotonic() of the most recent read
_cache_sweeper = None   # the eviction thread, started on first load


def _sweep_cache() -> None:
    """Release the cached dump once nothing has read it for the TTL.

    Dropping the reference here cannot disturb a tool that is already walking
    the list: that caller holds a reference of its own, so the data stays alive
    until it returns and is only then collected. This needs no lock of its own
    beyond keeping the two cache fields consistent with each other.
    """
    global _cache_key, _cache_units
    while True:
        time.sleep(_CACHE_SWEEP_SECONDS)
        with _cache_lock:
            if _cache_units is None:
                continue
            if time.monotonic() - _cache_read_at > _CACHE_TTL_SECONDS:
                _cache_key = None
                _cache_units = None


def _start_sweeper_once() -> None:
    """Start the eviction thread on first load. Caller must hold _cache_lock.

    Deferred rather than started at import so that a process which never reads a
    dump - every git tool, list_projects - spawns no thread at all.
    """
    global _cache_sweeper
    if _cache_sweeper is None:
        _cache_sweeper = threading.Thread(
            target=_sweep_cache, name="dump-cache-sweeper", daemon=True
        )
        _cache_sweeper.start()


def load_units(project: str = None) -> list:
    """Load the `units` array from the project's dump.

    Held in memory between calls, so a burst of tool calls parses the dump once
    instead of once each. The cached list is handed out BY REFERENCE: treat it
    as read-only. Nothing in tools/ mutates it today, and a caller that started
    would corrupt every later call in the same process.
    """
    _, proj = resolve_project(project)
    dump_path = proj["dump_path"]
    if not os.path.exists(dump_path):
        raise FileNotFoundError(
            f"Dump not found at {dump_path}. Run generate_dump first."
        )

    global _cache_key, _cache_units, _cache_read_at
    with _cache_lock:
        key = (os.path.abspath(dump_path), os.stat(dump_path).st_mtime_ns)
        if _cache_units is None or key != _cache_key:
            # Let go of the previous dump before parsing the next one, so that
            # switching projects never holds two parsed dumps at once. The read
            # happens under the lock on purpose: a second caller arriving mid-
            # parse waits and then hits the cache, rather than parsing its own
            # copy of the same file alongside this one.
            _cache_key = None
            _cache_units = None
            with open(dump_path, encoding="utf-8") as f:
                units = json.load(f)["units"]
            _cache_key = key
            _cache_units = units
            _start_sweeper_once()
        _cache_read_at = time.monotonic()
        return _cache_units


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


def deployment_model_path(project: str = None, filename: str = None) -> str:
    """Resolve a project key to its `deployment/model` directory, or a file in it.

    Everything under `deployment/` is a *build* artifact, not the model: Studio
    Pro writes it when the app is built, and it describes that build. It is
    gitignored, it is absent in a fresh clone, and it goes stale the moment the
    model changes without a rebuild. Callers are expected to report its mtime so
    the caller can tell whether it still matches what they are asking about.

    Derived from the parent directory of `mpr_path`, like `resolve_repo_path`.
    """
    key, proj = resolve_project(project)
    mpr_path = proj.get("mpr_path")
    if not mpr_path:
        raise ValueError(f"Project '{key}' has no 'mpr_path' in projects.json.")

    model_dir = os.path.join(os.path.dirname(mpr_path), "deployment", "model")
    if not os.path.isdir(model_dir):
        raise FileNotFoundError(
            f"'{model_dir}' does not exist (project '{key}'). It is created by a "
            "build: open the app in Studio Pro and run it once, or build it from "
            "the command line."
        )

    if filename is None:
        return model_dir

    path = os.path.join(model_dir, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"'{path}' not found (project '{key}'). The deployment directory "
            "exists but this file does not — the build may be incomplete."
        )
    return path


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
