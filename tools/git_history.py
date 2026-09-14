"""Commits of the Mendix app's repository, with the usual git filters."""

from collections import Counter

from tools._utils import git_text, resolve_repo_path

_FORMAT = "%H\t%an\t%ae\t%ai\t%s"


def _parse(output: str) -> list[dict]:
    commits = []
    for line in output.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 4)
        if len(parts) < 5:
            continue
        full_hash, name, email, date, message = parts
        commits.append({
            "hash": full_hash[:7],
            "full_hash": full_hash,
            "author_name": name,
            "author_email": email,
            "date": date,
            "message": message,
        })
    return commits


def git_history(
    project: str = None,
    author: str = None,
    since: str = None,
    until: str = None,
    limit: int = None,
    branch: str = None,
    summarize_by_author: bool = False,
    file: str = None,
) -> dict:
    """The repository's commits, newest first.

    Dates accept anything git accepts: '2024-01-01', '2 weeks ago',
    'last tuesday'. `summarize_by_author` returns counts per author instead of
    the list, and ignores `limit` - counting a truncated list would answer "who
    committed most" with a number that depends on where the list was cut.
    """
    try:
        repo = resolve_repo_path(project)
    except (ValueError, FileNotFoundError) as e:
        return {"success": False, "error": str(e)}

    args = ["log", f"--pretty=format:{_FORMAT}"]

    if branch:
        args.append(branch)
    if author:
        args.append(f"--author={author}")
    if since:
        args.append(f"--after={since}")
    if until:
        args.append(f"--before={until}")
    if limit and not summarize_by_author:
        args += ["-n", str(limit)]
    if file:
        args += ["--", file]

    try:
        output = git_text(repo, *args)
    except RuntimeError as e:
        return {"success": False, "error": str(e)}

    commits = _parse(output)

    if summarize_by_author:
        counted = Counter(c["author_name"] for c in commits)
        return {
            "success": True,
            "project": project,
            "repo": repo,
            "filters": {"since": since, "until": until, "branch": branch,
                        "file": file},
            "total_commits": len(commits),
            "by_author": [
                {"author": name, "total_commits": total}
                for name, total in counted.most_common()
            ],
        }

    return {
        "success": True,
        "project": project,
        "repo": repo,
        "filters": {
            "author": author,
            "since": since,
            "until": until,
            "limit": limit,
            "branch": branch,
            "file": file,
        },
        "total_commits": len(commits),
        "commits": commits,
    }


TOOL_DEFINITION = {
    "name": "git_history",
    "description": (
        "Return the commits of the Mendix app's git repository, newest first. "
        "Filter by author (name or e-mail, partial and case-insensitive), by date "
        "range, by branch, or by one file. Dates take any format git takes: "
        "'2024-01-01', '2 weeks ago', 'last tuesday'.\n\n"
        "Use summarize_by_author=true for 'who commits the most' or 'how many "
        "commits did X make'; use limit for 'the last N commit messages'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "project": {
                "type": "string",
                "description": "Project key in projects.json. Defaults to the configured default.",
            },
            "author": {
                "type": "string",
                "description": "Author name or e-mail, partial and case-insensitive.",
            },
            "since": {
                "type": "string",
                "description": "Start date. Any git format: '2024-01-01', '2 weeks ago'.",
            },
            "until": {
                "type": "string",
                "description": "End date, same formats.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of commits to return.",
            },
            "branch": {
                "type": "string",
                "description": "A specific branch. Defaults to the one checked out.",
            },
            "summarize_by_author": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Return commit counts per author instead of the commits. "
                    "Ignores 'limit', which would otherwise make the counts depend "
                    "on where the list was cut."
                ),
            },
            "file": {
                "type": "string",
                "description": (
                    "Repository-relative path of one file; only commits touching it "
                    "are returned. Combine with the security unit's $ID from "
                    "get_module_security to follow a module's role changes: the "
                    "path of a unit is mprcontents/<first two chars of the "
                    "$ID>/<next two>/<$ID>.mxunit."
                ),
            },
        },
        "required": [],
    },
}


if __name__ == "__main__":
    import pprint
    pprint.pprint(git_history(limit=5))
    pprint.pprint(git_history(summarize_by_author=True, since="3 months ago"))
