# Backlog

What is known to be missing, rough, or deliberately absent. One line per item, each
with the reason it is on the list — an item without a reason is an opinion, and
opinions do not survive the six months until somebody reads this again.

Three states, and the third one carries its weight:

- **Open** — worth doing, nobody has.
- **Blocked** — waiting on a decision, not on effort. The decision is named.
- **Settled against** — tried, measured, or argued, and rejected. These stay on the
  page forever. The recurring cost in this repo is not forgetting what to do next,
  it is re-proposing something that was already discarded for a reason that still
  holds.

Scope note: this file tracks the **engine**. A fieldbook's content is not engine
work and does not belong here.

Last reviewed against the tree at `733a9a3` — 20 registered tools, plus
`diff_mxunit` as an internal helper.

---

## Release hygiene

- [ ] **No `LICENSE`.** A public repo without one is "all rights reserved", which
      legally blocks the reuse the repo exists to allow. MIT closes it in one file.
      *Deliberately deferred by the owner, not forgotten.*
- [ ] **One admin.** Bus factor of one on the only copy that is meant to outlive
      the author's machine. Visibility does not fix this; a second commit bit does.
      *Also deliberately deferred.*
- [ ] **`README.md` still says "Status: Early … lands here after a cleanup pass".**
      The cleanup pass largely happened. The sentence now understates the repo and
      tells a first-time reader to wait for something that already arrived.
- [ ] **No committed tests.** Every tool here was validated by a real stdio
      handshake — spin up `server.py`, `initialize` → `tools/list` → `tools/call`
      — and every one of those harnesses was thrown away afterwards. The method is
      right and the throwing away is the problem: a regression suite that exists
      only in a scratch directory protects exactly one session. The handshake is
      what should be committed, not unit tests that swap the dump for a fake.
- [ ] **`TOOL_DEFINITION` is dead weight in all 20 tool files.** Nothing reads it
      (confirmed: no reference in `server.py` or `tools/_utils.py`). The
      descriptions the model actually sees are the docstrings of the `tool_*`
      functions in `server.py`. Editing a `TOOL_DEFINITION` expecting a behaviour
      change is a free trap for a contributor, and the trap is silent.

## Missing capability

- [ ] **Three analytical tools were never ported** from the private version:
      change search across history, entities reachable from screens, and
      microflows callable from the client. ~1400 lines, and the bulk of the
      app-specific assumptions in the original — which is why they were left for
      last rather than first.
- [ ] **The engine's own `knowledge/` is empty.** The directory ships, the design
      intends it (platform behaviour travels with the engine; app-specific
      behaviour lives in a fieldbook), and eight platform facts have already been
      triaged as belonging here. An engine that reads a `.mpr` is useful; one that
      knows a silent platform footgun is a different category — and that is the
      half that is publishable without exposing anybody's app.
      *Known cost: the strongest of those facts are written through the case that
      proved them, so separating mechanism from evidence is real editing and loses
      the most convincing part.*

## Rough edges in shipped tools

- [ ] **`resolve_operation_ids` has no niche.** Zero invocations since it shipped.
      Not under-used — duplicated: in the one session that was its exact scenario
      (dozens of operation ids on screen), the id-to-microflow translation was
      done by a hand-written script reading `operations.json` instead. Losing to a
      shell script in your own use case is the signal. *Proposed fix: make it
      internal, unregistered, the way `diff_mxunit` is. Offered, not requested.*
- [ ] **`create_fieldbook` has never run outside its own test.** Weaker evidence
      than the item above — it is a one-shot scaffold, so one use per fieldbook is
      the ceiling, and the fieldbook that exists was scaffolded through it. Listed
      so the zero is not mistaken for a second dead tool later.
- [ ] **Folder results do not say which module they are in.** Folders have no
      qualified name, so a name search returns several identical-looking rows.
      Fix is to walk `$ContainerID` up to the module (~10 lines; `entity_index`
      already does half of it). *Offered, not requested.*
- [ ] **Folder results drop whole unit types, and the reported `total` confirms the
      omission.** `NAVIGABLE_TYPES` is a 13-entry allowlist; every other type is
      skipped and `total` counts only the survivors, so a module's listing reads as
      complete when it is not. Measured on one module of a real app: the tool
      reported 38 units — 1 constant and 37 enumerations — while omitting both the
      consumed OData service document that *generated* those 37 enumerations and the
      domain model holding the external entities it created. The conclusion drawn
      from that listing — "this module has no entities and no logic" — was wrong,
      was written into a knowledge note on the strength of the tool's own total, and
      cost a separate investigation to catch. Also invisible: domain models,
      published and consumed service documents, import and export mappings, message
      definitions, JSON structures, workflows, and rules — rules being the sharp
      one, since `read_microflow` reads them, so the engine renders a unit type its
      own structure listing denies exists. Violates *omitting suppression is a bug*:
      nothing in the response says anything was left out. The fix is marking, not
      widening the allowlist — a count of unlisted units per folder, so the reader
      knows to ask, and the type label carries the rest.
- [ ] **Resolving N ids costs N round-trips.** The plural lookup was removed for
      lack of use, and that was right, but it did one thing the singular cannot:
      report per-id results instead of aborting on the first bad one. *If this
      ever hurts, the fix is accepting `str | list[str]` in `find_unit_by_id` —
      not resurrecting the removed tool.*
- [ ] **A stale dump is not announced.** The dump is derived from the `.mpr` and
      silently goes out of date the moment somebody saves the app. Every answer
      built on it then describes a model that no longer exists, confidently. The
      engine knows both timestamps and says nothing.
- [ ] **Accessibility properties dominate `read_page` output on small pages.**
      Measured at roughly a third of the text on one small page — a fixed cost per
      widget that crowds out the structure the reader actually asked for, exactly
      where the output should be shortest. Needs a rule for when they earn their
      space, not blanket removal: *omitting detail is design, omitting suppression
      is a bug* (see below).
- [ ] **`read_page` does not report the type of a local variable.** Page parameters
      print with their type; a local variable prints as `name = empty` and its type
      is dropped. Measured on one page with three local variables: two were
      different enumerations and the third a string, and all three rendered
      identically. The asymmetry is the whole point — it is what explains a filter
      behaving differently on one tab than on another — and it is precisely what is
      lost. Sibling of the flow-parameter item below, and deliberately a separate
      entry: different tool, different field, different fix.
- [ ] **`read_microflow` does not show where a return variable is written.**
      Reading a validation flow means answering "where does this get set", and
      today that is manual re-reading of the body. The linearizer already walks
      every activity; the write sites are in reach.
- [x] **`read_microflow` does not say whether a parameter is optional.** *Shipped:
      an optional parameter now prints `name: type (optional)`, with its default
      when it declares one.* The model carries `isRequired` and a `defaultValue`
      per flow parameter and the linearizer printed `name: type`, dropping both.
      Measured on a three-parameter flow where one parameter was optional with an
      enumeration default: all three printed identically, so the reader concludes
      every call site supplies it and goes looking for the bug somewhere else.
      This is omitted *suppression*, not omitted detail — nothing in the output
      said "unknown" (see the invariant below). Flow-only: page parameters carry
      the same field but it is constant there, so rendering it on pages would add
      noise and no information.
      The cost of marking is bounded by how rare the exception is: measured across
      4,019 flow parameters, **61 are optional (1.5%), in 53 of 2,867 flows
      (1.8%)** — so 98% of outputs are byte-identical, verified by rendering the
      same flows against the previous tree, and only the exception carries ink.
      Remeasuring moves the denominators and leaves the counts alone: a dump taken
      hours earlier gave 4,029 and 2,875, the same 61 optional in the same 53
      flows. Treat the totals as the vintage of one dump and the counts as the
      finding. Two facts that shaped the rendering: `isRequired` is never
      absent, so nothing has to be inferred; and **no required parameter anywhere
      carries a default**, which is what makes a default meaningful only on an
      optional one. A blank default is kept distinct from a default of `''` —
      32 of the 61 are blank — because the first leaves the argument empty and
      the second passes an empty string.

## Invariants worth not breaking

Not tasks. These were each paid for once and are cheap to violate by accident.

- **Omitting detail is design; omitting suppression is a bug.** Layout, UUIDs and
  defaults are thrown away on purpose. A flag that says *this does not run* is not
  detail — rendering a disabled step like a live one produced three consecutive
  confident wrong conclusions before a human caught it.
- **Mark, never filter.** A suppressed or excluded element stays in the response,
  in order, with numbering that still matches the canvas — it is frequently the
  thing being looked for. Marking is the whole of the fix.
- **The engine never versions a fieldbook.** No `git init`, stage, commit, push,
  remote, or merge request. Refusing to read a fieldbook whose branch disagrees
  with the working copy is reading, not versioning — and the engine never
  "repairs" that disagreement by touching a branch.
- **Nothing with an absolute machine path enters a versioned file.** This repo
  exists with fresh history because that rule was broken once in the one it
  replaced.

## Blocked on a decision

- [ ] **How a project tool reaches the engine** — the `ctx` the engine would
      inject. Without it, every fieldbook imports engine internals and the
      internals become public API on the day the first one does. Design it small,
      before there is a caller.
- [ ] **Name collision between a project tool and a built-in one.** An error at
      load time is safer than silent precedence either way, but which one wins has
      not been decided.
- [ ] **Running third-party `.py` from a fieldbook.** The engine would execute
      code from a team's repo. A mandatory merge request makes it manageable, and
      a conservative fallback exists: `schema_version: 1` with knowledge only, and
      `tools:` later.
- [ ] **A fieldbook clone ages silently.** The engine should *warn* that a clone
      is behind its origin and must not `pull` on its own — fetching third-party
      code and running it unasked is the automated version of the item above.
- [ ] **Language of fieldbook content.** Schema keys are English and settled. The
      body is not.

## Settled against — do not re-propose

- **A tool that lists units by type.** Written and discarded. Three compounding
  defects: fuzzy matching linked unrelated types, the limit truncated a list in
  dump order rather than by relevance, and the reported total was the truncated
  one. Measured result: an exact request for one type returned none of them.
  `search_text` already filters by type via substring with none of the three.
- **Ranking a name search by raw string similarity.** `SequenceMatcher` scores
  global string similarity, which penalises precisely what is normal in Mendix
  names: reordered tokens and length. Replaced by stem-and-tier matching after a
  qualified name scored below the cutoff while an unrelated short folder ranked
  above it. Also: weigh a partial match by **characters, not token count**, or two
  incidental hits beat one substantial one.
- **CSS class diagnosis in v1.** It drags in a Sass binding frozen since 2020 that
  fails on modern CSS — it cannot compile the stock UI framework, which is the
  one input that matters. Out on purpose, not overlooked.
- **Gating by microflow name.** The widget framework caps the parameter it can
  carry, and the modern widget API does not expose the name at all. Closed; do not
  reopen.
- **Sharing the dump cache between processes.** Each editor session starts its own
  server process; a single-slot in-process cache is a per-process ceiling by
  design, and the idle TTL is what frees memory. Crossing process boundaries
  needs an external daemon — rejected on complexity.
- **A fieldbook living inside the Mendix repo.** Studio Pro commits the whole
  working copy with no per-file staging, so prose would be swept into model
  commits, unreviewable and easy to ship half-written. The unit of commit is not
  yours. Measured once: thousands of model units in a real app, and the only
  markdown files were vendored third-party READMEs — not a missing habit, a
  missing path.
- **A fieldbook as one markdown file.** One file means a conflict on every merge
  request, which means nobody contributes twice.
- **Calling the fieldbook a "map".** The engine already produces a map — the dump,
  the structure, the cross-references. Two owners for one word creates a daily
  ambiguity, and the defining trait of this content is being what the model cannot
  state, which "map" asserts the opposite of.

---

## How to add an item

Say what is wrong, then why it matters, and prefer a number to an adjective — a
measurement is the only part of an item that still argues for itself later. If it
was decided against, move it down to the last section with the reason intact
rather than deleting it: a deleted item comes back as a proposal.

And nothing in here names anybody's app, module, entity, or file path. This file is
public.
