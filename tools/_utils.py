"""Shared helpers: configuration, dump loading, git, and response size capping."""

import functools
import inspect
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid

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


def _peek_units() -> list | None:
    """The cached units, or None - never triggers a parse.

    Used only to put names on ids in a staleness warning. A warning is not
    worth a few hundred MB of parsing, and by the time one is assembled the
    tool that is being stamped has already loaded the dump anyway.
    """
    with _cache_lock:
        return _cache_units


# --- is the dump still current? --------------------------------------------
#
# The dump is a snapshot, and nothing about reading it says how old it is. Left
# unsaid, every answer carries a date that nobody can see: a page was compared
# against a dump 25h older than the model and the output said nothing, and an
# investigation into whether a feature existed came back wrong because a whole
# block had been added after the dump ran.
#
# Two signals, in order of cost:
#
#   1. mtime - the .mpr is newer than the dump. Free, and enough to know that
#      the dump MIGHT be behind.
#   2. the unit hashes - the .mpr's one table carries a ContentsHash per unit,
#      so a snapshot taken when the dump ran tells exactly WHICH units moved.
#      ~9ms. Without it, a touched-but-unchanged .mpr reads as stale, which is
#      a false alarm; with it, that case is recognised and stays quiet.
#
# The snapshot is written by generate_dump. A dump made before this existed has
# none, and then only signal 1 is reported - stated as such rather than implied.

_mpr_lock = threading.Lock()
_mpr_cache_key = None   # (absolute .mpr path, mtime_ns) the hashes came from
_mpr_cache = None       # {unit id: ContentsHash}

_INDEX_SCHEMA = 1


def dump_index_path(dump_path: str) -> str:
    """Where the unit-hash snapshot for a dump lives."""
    return os.path.splitext(dump_path)[0] + ".index.json"


def _mpr_unit_hashes(mpr_path: str) -> dict | None:
    """{unit id: ContentsHash} straight from the .mpr, or None if unreadable.

    The .mpr is SQLite with a single table, but Studio Pro holds it open, so it
    is copied before being read. The copy is named per process: several server
    processes run at once, one per editor session.
    """
    global _mpr_cache_key, _mpr_cache
    try:
        stat = os.stat(mpr_path)
    except OSError:
        return None
    key = (os.path.abspath(mpr_path), stat.st_mtime_ns)

    with _mpr_lock:
        if key == _mpr_cache_key:
            return _mpr_cache
        probe = os.path.join(
            tempfile.gettempdir(), f"mendix-mapper-mpr-{os.getpid()}.probe"
        )
        try:
            shutil.copy2(mpr_path, probe)
            connection = sqlite3.connect(probe)
            try:
                rows = connection.execute(
                    "SELECT UnitID, ContentsHash FROM Unit"
                ).fetchall()
            finally:
                connection.close()
        except Exception:
            return None

        hashes = {}
        for unit_id, contents_hash in rows:
            try:
                hashes[str(uuid.UUID(bytes_le=unit_id))] = contents_hash
            except Exception:
                continue
        _mpr_cache_key = key
        _mpr_cache = hashes
        return hashes


def write_dump_index(dump_path: str, mpr_path: str) -> bool:
    """Snapshot the .mpr's unit hashes next to a dump. True when written.

    Call this BEFORE running `mx`, never after. An edit landing mid-dump would
    otherwise be recorded as already included, and the dump would report itself
    current while holding the older model. Snapshotting first makes that same
    race produce a false alarm instead, which is the harmless direction.
    """
    hashes = _mpr_unit_hashes(mpr_path)
    if hashes is None:
        return False
    try:
        with open(dump_index_path(dump_path), "w", encoding="utf-8") as f:
            json.dump(
                {"schema": _INDEX_SCHEMA, "mpr_path": mpr_path, "units": hashes},
                f,
            )
        return True
    except OSError:
        return False


def _read_dump_index(dump_path: str) -> dict | None:
    try:
        with open(dump_index_path(dump_path), encoding="utf-8") as f:
            index = json.load(f)
    except (OSError, ValueError):
        return None
    if index.get("schema") != _INDEX_SCHEMA:
        return None
    units = index.get("units")
    return units if isinstance(units, dict) else None


def _age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} seconds"
    if seconds < 90 * 60:
        return f"{round(seconds / 60)} minutes"
    if seconds < 36 * 3600:
        return f"{round(seconds / 3600)} hours"
    return f"{round(seconds / 86400)} days"


def _named(unit_ids: list, limit: int = 10) -> list:
    """Qualified names for unit ids, falling back to the bare id."""
    units = _peek_units()
    names = {}
    if units:
        wanted = set(unit_ids)
        for unit in units:
            if unit.get("$ID") in wanted:
                names[unit["$ID"]] = (
                    unit.get("$QualifiedName") or unit.get("name") or unit["$ID"]
                )
    listed = [names.get(i, i) for i in unit_ids[:limit]]
    if len(unit_ids) > limit:
        listed.append(f"... and {len(unit_ids) - limit} more")
    return listed


def dump_staleness(project: str = None) -> dict | None:
    """What to say about this dump's age, or None when there is nothing to say.

    None means the dump is current - either the .mpr is no newer than the dump,
    or it is newer but no unit's contents actually changed. Anything else comes
    back as a dict meant to be attached to a tool's response verbatim.
    """
    try:
        _, proj = resolve_project(project)
    except (ValueError, FileNotFoundError):
        return None

    dump_path = proj.get("dump_path")
    mpr_path = proj.get("mpr_path")
    if not dump_path or not os.path.exists(dump_path):
        return None             # tools already refuse a missing dump
    if not mpr_path:
        return None             # nothing configured to compare against

    if not os.path.exists(mpr_path):
        return {
            "checked": False,
            "advice": (
                f"Cannot tell whether this dump is current: 'mpr_path' points at "
                f"{mpr_path}, which does not exist. Fix it in projects.json."
            ),
        }

    try:
        dump_time = os.stat(dump_path).st_mtime
        mpr_time = os.stat(mpr_path).st_mtime
    except OSError:
        return None
    if mpr_time <= dump_time:
        return None

    snapshot = _read_dump_index(dump_path)
    current = _mpr_unit_hashes(mpr_path)

    report = {
        "dump_generated": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(dump_time)),
        "model_modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(mpr_time)),
        "behind_by": _age(mpr_time - dump_time),
    }

    if snapshot is None or current is None:
        report["changed_units"] = "unknown"
        report["advice"] = (
            "The model was modified after this dump was generated, so anything "
            "above may be out of date. Which units changed cannot be listed: "
            + ("this dump predates unit-hash snapshots - regenerating it will "
               "make the next warning precise."
               if snapshot is None else
               "the .mpr could not be read.")
            + " Run generate_dump to refresh."
        )
        return report

    changed = [u for u, h in current.items() if u in snapshot and snapshot[u] != h]
    added = [u for u in current if u not in snapshot]
    removed = [u for u in snapshot if u not in current]

    if not changed and not added and not removed:
        # The .mpr was rewritten without any unit's contents moving - Studio Pro
        # touches it for things that are not model edits. Not stale.
        return None

    report["changed_units"] = len(changed) + len(added) + len(removed)
    if changed:
        report["changed"] = _named(changed)
    if added:
        report["added"] = len(added)
    if removed:
        report["removed"] = _named(removed)
    report["advice"] = (
        f"{report['changed_units']} unit(s) changed in the model after this dump "
        "was generated. Anything above reflects the older model. Run "
        "generate_dump to refresh - or disregard this if the units listed are "
        "unrelated to the question."
    )
    return report


def dump_backed(fn):
    """Mark a tool as reading the dump, so its response reports a stale one.

    Stamping here rather than inside each tool keeps one rule in one place, and
    keeps it on failure paths too: "no unit found with that name" is exactly the
    answer a stale dump produces, and the reason belongs next to it.

    Wraps with functools.wraps so that the signature and docstring FastMCP reads
    to build the tool's schema stay those of the function underneath.
    """
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        response = fn(*args, **kwargs)
        try:
            if not isinstance(response, dict) or "stale_dump" in response:
                return response
            bound = signature.bind_partial(*args, **kwargs)
            stale = dump_staleness(bound.arguments.get("project"))
            if stale:
                return {**response, "stale_dump": stale}
        except Exception:
            # A warning is never worth failing a working call over.
            pass
        return response

    return wrapper


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


def languages(project: str = None) -> tuple[str, list[str]]:
    """Return (default language code, every configured code) for the app.

    A multi-language app holds one translation per language on every caption,
    and nothing in a page says which of them the users actually read. The answer
    is a project-level setting, so any tool that renders a caption has to come
    here for it rather than take the first translation it finds and hope.

    Both halves are returned because the default alone cannot tell a caller it
    asked for a language the app does not have.
    """
    units = load_units(project)

    for unit in units:
        if unit.get("$Type") != "Settings$ProjectSettings":
            continue
        for part in unit.get("settingsParts") or []:
            if part.get("$Type") != "Settings$LanguageSettings":
                continue
            codes = [
                language["code"]
                for language in part.get("languages") or []
                if language.get("code")
            ]
            return part.get("defaultLanguageCode") or "", codes

    return "", []


def mark_excluded(entry: dict, unit: dict) -> dict:
    """Flag an identity entry whose unit is excluded from the build.

    An excluded document is still in the model and still matches every search,
    but Studio Pro does not deploy it - it runs nowhere. The name is not a
    reliable tell: plenty of them carry no _BackUp or _Deprecated suffix, so a
    listing that stays quiet reads as if the unit were live, and a caller asking
    what calls a microflow gets an answer inflated by callers that cannot call
    anything.

    The flag is added, never a filter: an excluded unit is often exactly what
    the caller is looking for. Omitted when false so the common entry stays small.
    """
    if unit.get("excluded"):
        entry["excluded"] = True
    return entry


def excluded_count(entries: list) -> int:
    """How many of these identity entries `mark_excluded` flagged."""
    return sum(1 for e in entries if e.get("excluded"))


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
