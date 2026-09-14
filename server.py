from mcp.server.fastmcp import FastMCP

from tools.create_fieldbook import create_fieldbook
from tools.find_unit_by_id import find_unit_by_id
from tools.find_unit_by_name import find_unit_by_name
from tools.find_units_by_ids import find_units_by_ids
from tools.find_units_by_similar_name import find_units_by_similar_name
from tools.find_units_that_use import find_units_that_use
from tools.generate_dump import generate_dump
from tools.get_module_security import get_module_security
from tools.git_diff_commits import git_diff_commits
from tools.git_history import git_history
from tools.git_unit_history import git_unit_history
from tools.git_units_between_commits import git_units_between_commits
from tools.list_folder_structure import list_folder_structure
from tools.list_modules import list_modules
from tools.list_projects import list_projects
from tools.list_user_roles import list_user_roles
from tools.search_text import search_text
from tools.write_knowledge import write_knowledge

mcp = FastMCP("Mendix Mapper")


# --- setup -----------------------------------------------------------------

@mcp.tool()
def tool_list_projects() -> dict:
    """
    List the Mendix projects configured on this machine.
    Shows each project key (used as the 'project' argument of the other tools),
    its display name, whether it is the default, and whether its dump exists.
    Call this first when you do not know which project to use.
    """
    return list_projects()


@mcp.tool()
def tool_generate_dump(project: str = None) -> dict:
    """
    Regenerate the JSON dump of a Mendix app from its .mpr file.
    Call this when the working copy has changed and the answers should reflect
    the latest model — the dump does not refresh itself.
    If 'project' is omitted, the configured default is used.
    """
    return generate_dump(project)


# --- finding units ---------------------------------------------------------

@mcp.tool()
def tool_find_unit_by_name(name: str, project: str = None) -> dict:
    """
    Find units by exact name.
    Accepts a simple name ('ACT_Save') or a qualified one ('MyModule.ACT_Save').
    A simple name may match several modules, so prefer the qualified form when
    you know the module.
    If 'project' is omitted, the configured default is used.
    """
    return find_unit_by_name(name, project)


@mcp.tool()
def tool_find_unit_by_id(id: str, project: str = None) -> dict:
    """
    Return the full JSON of a single unit by its exact $ID (a UUID).
    Use it when you already have an ID and want the unit's details.
    If 'project' is omitted, the configured default is used.
    """
    return find_unit_by_id(id, project)


@mcp.tool()
def tool_find_units_by_ids(ids: list[str], project: str = None) -> dict:
    """
    Resolve several unit $IDs at once.
    IDs that do not exist are flagged individually instead of failing the call.
    If 'project' is omitted, the configured default is used.
    """
    return find_units_by_ids(ids, project)


@mcp.tool()
def tool_find_units_by_similar_name(name: str, limit: int = 5,
                                    project: str = None) -> dict:
    """
    Fuzzy-search units by name.
    Use it when the exact name is unknown or possibly misspelled; it returns the
    closest matches.
    If 'project' is omitted, the configured default is used.
    """
    return find_units_by_similar_name(name, limit, project)


@mcp.tool()
def tool_search_text(query: str, module: str = None, unit_type: str = None,
                     case_sensitive: bool = False, project: str = None) -> dict:
    """
    Search for units whose JSON contains a given string.
    This is what replaces grep on a codebase stored as binary: page labels,
    button captions, hardcoded strings in microflows, attribute names.
    Optional filters narrow it to one module or one $Type.
    If 'project' is omitted, the configured default is used.
    """
    return search_text(query, module, unit_type, case_sensitive, project)


# --- understanding the app -------------------------------------------------

@mcp.tool()
def tool_find_units_that_use(name: str = None, id: str = None,
                             project: str = None) -> dict:
    """
    Return every unit that references the given one.
    This is the impact question: which microflows, pages or entities depend on
    it, and therefore what a change might break. Accepts an exact name or an $ID.
    If 'project' is omitted, the configured default is used.
    """
    return find_units_that_use(name, id, project)


@mcp.tool()
def tool_list_folder_structure(module: str = None, folder: str = None,
                               project: str = None) -> dict:
    """
    Return the folder tree with the units inside each folder.
    Filter by module and/or folder name (partial, case-insensitive).
    Use it to see how the app is organised, or what lives in a given folder.
    If 'project' is omitted, the configured default is used.
    """
    return list_folder_structure(module, folder, project)


@mcp.tool()
def tool_list_modules(source: str = "all", project: str = None) -> dict:
    """
    List the app's modules.
    'source' filters by origin: 'all' (default), 'marketplace' or 'own'.
    Each entry carries the module $ID, its name, and whether it is marked as a
    UI resources module.
    If 'project' is omitted, the configured default is used.
    """
    return list_modules(source, project)


# --- security --------------------------------------------------------------

@mcp.tool()
def tool_list_user_roles(project: str = None) -> dict:
    """
    Return the app's user roles, with the module roles each one aggregates and
    the roles it is allowed to manage.
    If 'project' is omitted, the configured default is used.
    """
    return list_user_roles(project)


@mcp.tool()
def tool_get_module_security(module: str, project: str = None) -> dict:
    """
    Return a module's security unit and the module roles defined in it.
    Also returns the security unit's $ID, which is what lets you follow role
    changes through git history.
    If 'project' is omitted, the configured default is used.
    """
    return get_module_security(module, project)


# --- git history -----------------------------------------------------------

@mcp.tool()
def tool_git_history(project: str = None, author: str = None, since: str = None,
                     until: str = None, limit: int = None, branch: str = None,
                     summarize_by_author: bool = False, file: str = None) -> dict:
    """
    Return the commits of the app's git repository, newest first.
    Filter by author (name or e-mail, partial), date range, branch or a single
    file. Dates take any format git takes: '2024-01-01', '2 weeks ago'.
    Use summarize_by_author=true for 'who commits the most'; it ignores 'limit'.
    If 'project' is omitted, the configured default is used.
    """
    return git_history(project, author, since, until, limit, branch,
                       summarize_by_author, file)


@mcp.tool()
def tool_git_unit_history(unit: str, project: str = None, author: str = None,
                          since: str = None, until: str = None,
                          branch: str = None, limit: int = None,
                          include_merges: bool = False,
                          classify: bool = True) -> dict:
    """
    Return the commits that changed one unit, newest first — 'who changed X',
    'when was X last touched'. Identify it by $ID, qualified name or plain name;
    an ambiguous plain name comes back with the candidates.
    With classify=true (default) each commit says what kind of change it was:
    created, changed (logic), moved (only dragged on the canvas), or no_diff.
    If 'project' is omitted, the configured default is used.
    """
    return git_unit_history(unit, project, author, since, until, branch, limit,
                            include_merges, classify)


@mcp.tool()
def tool_git_diff_commits(project: str = None, ref_a: str = "HEAD~1",
                          ref_b: str = "HEAD") -> dict:
    """
    Compare two git refs and report what changed in the model, not in the bytes.
    One entry per .mxunit touched: status (created, modified, deleted) and how
    many properties differ. The property-level detail is written to temp/diffs/
    and pointed at by 'output_file' — read that file for the specifics.
    For 'the last N commits' use ref_a='HEAD~N'.
    If 'project' is omitted, the configured default is used.
    """
    return git_diff_commits(project, ref_a, ref_b)


@mcp.tool()
def tool_git_units_between_commits(project: str = None, ref_a: str = "HEAD~1",
                                   ref_b: str = "HEAD") -> dict:
    """
    Return the microflows, nanoflows and pages created, moved and changed
    between two refs. 'moved' means the only differences are positional —
    somebody dragged things on the canvas and no behaviour changed.
    Use it to review a branch or a day's work without reading every property.
    If 'project' is omitted, the configured default is used.
    """
    return git_units_between_commits(project, ref_a, ref_b)


# --- knowledge -------------------------------------------------------------

@mcp.tool()
def tool_create_fieldbook(path: str, name: str = None, branch: str = None,
                          register: bool = False, project: str = None) -> dict:
    """
    Create an empty fieldbook — the project-specific knowledge a Mendix app's
    model cannot state — and optionally register it in projects.json.
    Writes the manifest, a knowledge/ directory and a README saying what belongs
    in it. 'branch' defaults to the branch the working copy is currently on.
    Refuses a non-empty directory, and refuses anywhere inside the Mendix working
    copy — a fieldbook needs a repository of its own to be reviewable.
    It does not run git init, commit, or create anything remote.
    If 'project' is omitted, the configured default is used.
    """
    return create_fieldbook(path, name, branch, project, register)


@mcp.tool()
def tool_write_knowledge(title: str, body: str, entities: list[str] = None,
                         modules: list[str] = None, author: str = None,
                         target: str = "fieldbook", slug: str = None,
                         project: str = None) -> dict:
    """
    Create or update one knowledge entry — what the model itself cannot tell you.
    Use it for the things a dump can never answer: a role that looks orphaned but
    is not, two fields that look interchangeable and are not, a name the business
    uses that has no entity behind it.
    Writing the same title twice updates that entry instead of duplicating it.
    Every name in 'entities' is checked against the dump, and the fieldbook's
    branch is checked against the working copy's, before anything is written.
    Nothing is committed: the file lands in the working tree for you to review.
    Use target='engine' only for knowledge true of any Mendix app.
    If 'project' is omitted, the configured default is used.
    """
    return write_knowledge(title, body, entities, modules, author, target, slug,
                           project)


if __name__ == "__main__":
    mcp.run(transport="stdio")
