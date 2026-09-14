# Getting started

Setting this up means four things: get the code, build a virtual environment, write one config file, and register the server with your AI client. Budget ten minutes.

This guide is written to be followed either by a person or by an AI assistant driving a terminal. If you are an AI assistant, read [Notes for AI assistants](#notes-for-ai-assistants) at the bottom first — there are values here you cannot guess and must ask for.

---

## What you need first

| | Check it with | Expected |
|---|---|---|
| Python 3.10+ | `python --version` | `Python 3.10` or higher |
| Mendix Studio Pro | the command below | at least one version |
| A Mendix working copy | you already have one if you open the app in Studio Pro | a folder containing a `.mpr` file |

```powershell
Get-ChildItem "C:\Program Files\Mendix" -Directory |
  Where-Object { Test-Path (Join-Path $_.FullName "modeler\mx.exe") } |
  Select-Object -ExpandProperty Name
```

Filter on `mx.exe` rather than just listing the folder: `C:\Program Files\Mendix` also contains things that look like versions but are not, such as `Version Selector` and a bundled `gradle`.

The server reads the model through Studio Pro's `mx.exe`, so Studio Pro must be installed — but it never has to be *running*.

Windows only for now: the paths and `mx.exe` handling assume it.

---

## 1. Get the code

```powershell
git clone https://github.com/ramonbastoss/mendix-mapper.git C:\mcp\mendix-mapper
cd C:\mcp\mendix-mapper
```

The location is up to you, but pick something short and stable — it goes into your MCP client's config, and moving it later means editing that too. `C:\mcp\` is a reasonable default and keeps the path out of OneDrive-synced folders, which you want: the virtual environment and the generated dumps are large and have no business being synced.

## 2. Build the virtual environment

```powershell
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Verify:

```powershell
.\venv\Scripts\python.exe -c "import mcp; print('ok')"
```

Use `.\venv\Scripts\python.exe` explicitly rather than activating the environment. The MCP client will invoke that exact executable, so it is the one that has to work.

## 3. Write your `projects.json`

```powershell
Copy-Item projects.example.json projects.json
```

Then edit it. This file is gitignored and stays on your machine — every path in it is true only for you.

**Each entry is one working copy, not one app.** If you keep a separate checkout per branch (a `Dev` folder and an `Acceptance` folder), each one is its own entry with its own dump.

```json
{
  "default": "myapp-dev",
  "projects": {
    "myapp-dev": {
      "name": "MyApp - Dev",
      "mpr_path": "C:\\Users\\you\\Mendix\\MyApp-Dev\\MyApp.mpr",
      "dump_path": "C:\\mcp\\mendix-mapper\\dump\\myapp-dev.json",
      "mx_path": "C:\\Program Files\\Mendix\\10.24.13.86719\\modeler\\mx.exe",
      "fieldbook_path": ""
    }
  }
}
```

| Key | What it is |
|---|---|
| `mpr_path` | your app's `.mpr`. The only real input — the git repository is derived from its parent folder |
| `dump_path` | where the generated model dump is written. Expect 100–300 MB for a mid-sized app |
| `mx_path` | the `mx.exe` of the Studio Pro version **that matches this app** (see below) |
| `fieldbook_path` | optional, leave `""` for now — project-specific knowledge, not wired up yet |

Backslashes must be doubled: JSON treats `\` as an escape character.

### Finding the values

Locate your working copies:

```powershell
Get-ChildItem "$env:USERPROFILE\Mendix" -Filter *.mpr -Recurse -Depth 2 | Select-Object FullName
```

Then ask the app itself which Studio Pro version it needs:

```powershell
& "C:\Program Files\Mendix\<any-version>\modeler\mx.exe" show-version "C:\path\to\MyApp.mpr"
```

It prints a version string such as `10.24.13.86719`, which is exactly the folder name under `C:\Program Files\Mendix\`. Use that version's `mx.exe` in `mx_path`. This matters: a newer `mx.exe` will refuse to read an older app, and an older one cannot read a newer app at all.

## 4. Register the server

For Claude Code:

```powershell
claude mcp add mendix-mapper -s user -- "C:\mcp\mendix-mapper\venv\Scripts\python.exe" "C:\mcp\mendix-mapper\server.py"
```

`-s user` makes the server available in every folder on your machine, which is what you want — the app you are asking about is configured in `projects.json`, not by whatever directory you happen to be in.

For other MCP clients, register a **stdio** server with the same two arguments: the venv's `python.exe`, then `server.py`.

## 5. Verify

Without any client, straight from the repo:

```powershell
.\venv\Scripts\python.exe -m tools.list_projects
```

You should get `"success": true` and your project listed, with `"dump_exists": false` — you have not generated a dump yet.

Then, through your AI client, ask it to list the configured Mendix projects. If it comes back with your project, the connection works.

## 6. Generate the first dump

Ask your assistant to generate the dump, or run it directly:

```powershell
.\venv\Scripts\python.exe -m tools.generate_dump
```

This runs `mx dump-mpr` and takes **a few minutes**, writing a file of **100–300 MB**. That is normal.

The dump is a snapshot. It does not refresh itself when you change the model in Studio Pro, and nothing warns you that it is stale — regenerate it after pulling changes or after a significant editing session.

---

## Troubleshooting

**`projects.json not found`**
Step 3 was skipped, or the file is not at the repository root next to `server.py`.

**`ModuleNotFoundError: No module named 'mcp'`**
Something is running the system Python instead of the virtual environment. Check that your MCP registration points at `venv\Scripts\python.exe` and not at a bare `python`.

**`ModuleNotFoundError: No module named 'mcp.server.fastmcp'`**
You have `mcp` 2.x, which removed that module. `requirements.txt` pins `mcp[cli]<2` for this reason — reinstall with the pin.

**`'...mx.exe' not found`**
`mx_path` is wrong. Confirm the file exists at that exact path; the version folder name must match precisely.

**`.mpr file not found`**
`mpr_path` is wrong. Watch for single backslashes — JSON needs them doubled.

**`mx dump-mpr failed: ...`**
Usually a Studio Pro version mismatch. Run `mx show-version` against the `.mpr` (see step 3) and point `mx_path` at that version.

**`Dump not found at ... Run generate_dump first`**
Expected before step 6. If it persists afterwards, check that `dump_path` in the config is the same path the dump was written to.

**The server is registered but the assistant sees no tools**
Restart the client — MCP servers are launched at startup. Then check the server starts on its own: `.\venv\Scripts\python.exe server.py` should sit there waiting on stdin rather than exiting with a traceback.

---

## Notes for AI assistants

If you are setting this up on someone's behalf, the failure mode to avoid is inventing paths. Three values cannot be derived from this repository and must come from the machine or the user:

1. **Where the working copy is.** Run the `Get-ChildItem` discovery command from step 3. If it returns several `.mpr` files, ask which one — do not pick the first.
2. **Which Studio Pro version the app needs.** Never assume it is the newest installed. Run `mx show-version` against the `.mpr` and use what it prints.
3. **Where to install.** Confirm the target folder with the user before cloning; it ends up in their client config.

Other things worth knowing:

- **Verify after each step instead of at the end.** Every step above has a check that either passes or fails cleanly. A wrong path found at step 3 is thirty seconds of work; the same error surfacing as a broken MCP connection at step 5 is a confusing hunt.
- **Warn before generating the dump.** It takes minutes and writes hundreds of megabytes. Say so first rather than leaving the user watching a frozen terminal.
- **Do not edit `projects.json` blind.** Read it, show the user what you intend to change, and preserve entries you did not create — a person may have several working copies configured.
- **Never version a fieldbook on the user's behalf.** The fieldbook tools write files and stop; staging, committing, branching and pushing are the user's, and this server has no business doing them. When one of those tools succeeds, say that the change is unstaged and uncommitted rather than letting the user assume it is saved somewhere.
- **If a command fails, match the error against the troubleshooting section above** before improvising. Most failures here are one of six known causes, and guessing tends to add a second problem on top of the first.
