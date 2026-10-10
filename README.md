# mendix-mapper

MCP server that makes a Mendix app queryable by Claude — entities, microflows, pages, references, and semantic diffs of `.mxunit` across git history, without opening Studio Pro.

## Why

A Mendix project is binary. `git diff` tells you nothing, text search finds nothing, and figuring out the impact of a change means opening Studio Pro and clicking around.

This server fixes both ends of that:

- it extracts the model from the `.mpr` and exposes it as queryable data;
- it decodes `.mxunit` files (BSON) so commits can actually be diffed — including telling apart logic changes from things that merely moved on the canvas.

## What you can ask

Once the server is connected, questions like these become answerable without leaving the conversation:

- What uses `MyModule.MyEntity`? What breaks if I change it?
- Which microflows write to this attribute?
- Who changed this microflow, and when? What did they change?
- Which entities are reachable from the client, and what access rules do they have?
- Which pages and nanoflows can trigger this microflow?
- The browser sent `operationId: "Ab3xK9/uQ1W+mNpZrS4tLg"` — which microflow is that? (Recording a user journey in the network tab and resolving the ids turns it into the list of microflows the journey actually ran.)

## Setup

See **[GETTING_STARTED.md](GETTING_STARTED.md)** — clone, virtual environment, one config file, and registering the server. About ten minutes.

Requirements:

- Python 3.10+
- Mendix Studio Pro installed (the server shells out to `mx.exe` to dump the `.mpr`; Studio Pro never needs to be running)
- Windows (paths and `mx.exe` are Windows-oriented today)

## Fieldbooks

Everything above is read off the model. A **fieldbook** is the other half: the things about one app that its model cannot state — a role that looks orphaned but is not, two date fields that are not interchangeable, a name the business uses that has no entity behind it.

A fieldbook is a directory with a `fieldbook.yaml` manifest and one markdown file per topic — never a single file, since a single file means a conflict on every merge request and nobody contributes twice. The manifest is the entry point: no manifest, or a `schema_version` this engine does not support, and the engine refuses the fieldbook instead of guessing its layout.

Two tools handle it: `create_fieldbook` scaffolds an empty one, and `write_knowledge` writes a single markdown file into it.

What goes *inside* an entry is yours — `write_knowledge` writes the body through untouched, with no front-matter and no imposed structure. What it does enforce is everything around the entry: the manifest and its `schema_version`, the fieldbook's branch against the working copy's, a path that stays inside `knowledge/` and names a `.md` file, and a body free of anybody's absolute machine path.

The path is the whole of the layout: `iteration-rule.md` lands at the root of `knowledge/`, `IterationRules/iteration-rule.md` creates that subfolder. Grouping is therefore a convention between contributors, which also means a typo in a folder name grows a second group instead of failing — reuse the folders that exist.

### A rule can be settled before it is built

Some of what is worth writing down is a decision the app has not caught up with yet. So a fresh `knowledge/` is scaffolded with two shelves — `implemented/` and `not-implemented/` — and **the directory is the status.** Not a `status:` field, and never a body that hedges "in the future there will be…": either of those means that the day a rule ships, somebody has to find every paragraph about it and reword it, which is how a fieldbook starts lying. Entries are written in the present tense, as the rule; shipping one costs a `git mv`.

This one is a habit rather than a check — an entry reaches a shelf because somebody wrote `implemented/` in the path. The cost of being wrong is lopsided: anything on `implemented/` is read as a description of the app as it stands, so when in doubt, file it as not-implemented.

### The server does not version your fieldbook

**It creates and edits the directory on disk. That is the whole of it.**

It does not run `git init`, does not stage, does not commit, does not push, and does not create or talk to any remote. After a tool writes, the change is sitting unstaged in your working tree, and what happens to it is entirely yours: reviewing the diff, committing, branching, pushing, opening a merge request.

This is deliberate. A knowledge entry becomes a commit under somebody's name; deciding that it is worth committing is a judgement the server has no business making on your behalf.

Two consequences worth stating plainly:

- **Nothing is backed up until you commit it.** A fieldbook that was never put under version control is a folder like any other.
- **Keeping it in step with the app is manual.** The manifest declares which branch of the app it describes, and the server refuses to read a fieldbook whose branch does not match the working copy — but it will not move, merge, or check out anything to fix a mismatch. Carrying knowledge across branches is done the same way as carrying code across branches: by hand, by you.

## Status

Early. The code is being extracted from a private working version and lands here after a cleanup pass — the API is expected to change until the first tagged release.
