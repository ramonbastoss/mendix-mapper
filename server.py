from mcp.server.fastmcp import FastMCP

from tools.generate_dump import generate_dump
from tools.list_projects import list_projects

mcp = FastMCP("Mendix Mapper")


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


if __name__ == "__main__":
    mcp.run(transport="stdio")
