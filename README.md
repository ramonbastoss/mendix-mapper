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

## Requirements

- Python 3.10+
- Mendix Studio Pro installed (the server shells out to `mx.exe` to dump the `.mpr`)
- Windows (paths and `mx.exe` are Windows-oriented today)

## Status

Early. The code is being extracted from a private working version and lands here after a cleanup pass — the API is expected to change until the first tagged release.

Planned next: **fieldbooks** — a per-project package of business rules and project-specific tools, so the server can answer what the model itself cannot express.
