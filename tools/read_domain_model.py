"""Read a domain model as text instead of as the model's raw JSON.

The third member of the read_* family, and the one whose raw form is least
readable of the three. A domain model in the dump is a *drawing* of a database
schema: every entity carries its canvas coordinates, every attribute and
association a storage GUID, and 40.1% of the median unit's characters are UUIDs,
`$` keys and fields left at their default. Measured on a real app: 73 domain
models, median 9.6k tokens, largest 218k tokens - which no context window takes.

Three things make the raw unit specifically hard to read, and all three are
joins the model leaves to the reader:

  * an association's `parent` and `child` are **UUIDs**, so the raw JSON says
    which two entities are related without naming either of them. This is the
    join that gets done by hand in a shell script, and it is why this tool
    exists;
  * `persistable` and the four system members live ONLY on the entity at the
    root of an inheritance chain, in its `NoGeneralization` node. A specialized
    entity carries none of them, so reading them off the entity you asked about
    classifies every specialization as non-persistent. The chain is walked here;
  * who *specializes* an entity, and which associations point *at* it, are
    nowhere in the entity: the first is a field on every other entity, the
    second lives in whichever module declared it. Both are gathered across the
    whole app for a single-entity read.

Access rules are the bulk and are off by default. On the fattest entity measured
they were 84.6% of its 99k characters - ten rules whose member lists repeat the
entity's whole attribute list once per rule. They are never silently dropped:
the entity's `rules` line always says how many exist and how to print them, and
`rules none` is printed explicitly, because an entity with no rule at all is a
fact and not a missing line.

Two things do not survive, and neither is a question about a domain model:
canvas geometry, and the storage GUIDs. Either one is a reason to want the raw
unit - and reaching it is itself awkward, which is why the answer is handed
back rather than described. A domain model has no name and no qualified name,
only a `$ContainerID` pointing at its module, so find_unit_by_name cannot be
asked for one: a module name resolves to the `Projects$Module` unit, which does
not contain the domain model. `notes` therefore carries the domain model's own
`$ID`, which find_unit_by_id takes directly.
"""

from tools._utils import languages, load_units

# A documentation string is written for a human and is occasionally an essay.
# Attribute and rule documentation is printed inline, where a long one would
# bury the schema, so it is capped tighter than the entity's own.
DOC_LIMIT = 600
INLINE_DOC_LIMIT = 300

# Past this many characters, a response is worth a word of warning rather than
# just a return. Only access_rules=true over a whole module reaches it: measured
# at 208k characters on the largest module of a real app, against 46k for the
# same module without rules.
BULK_WARNING = 60_000

# An OQL view entity carries its whole query. It is the definition of that
# entity - there is nothing else to read - so it is printed, but a reporting
# query can run to pages and the cut is announced inline.
OQL_LIMIT = 1200

_SYSTEM_MEMBERS = (
    ("hasCreatedDate", "createdDate"),
    ("hasChangedDate", "changedDate"),
    ("hasOwner", "owner"),
    ("hasChangedBy", "changedBy"),
)

# Mendix spells out the delete rules in full; they are the single noisiest
# repeated string in the unit and they appear on every association in the app.
# The verbs below are the same statement, shorter.
_DELETE_BEHAVIOR = {
    "DeleteMeButKeepReferences": "keep",
    "DeleteMeAndReferences": "cascade",
    "DeleteMeIfNoReferences": "block",
}


def _flat(value, limit: int) -> str:
    """Collapse a value onto one line and cap a runaway literal."""
    if value is None or isinstance(value, bool):
        return ""
    flat = " ".join(str(value).split())
    if len(flat) > limit:
        return flat[:limit] + f"...(+{len(flat) - limit} chars)"
    return flat


def _short(kind: str) -> str:
    """'DomainModels$StringAttributeType' -> 'String'."""
    name = (kind or "?").split("$")[-1]
    for suffix in ("AttributeType", "RuleInfo", "Value", "Source"):
        if name.endswith(suffix) and name != suffix:
            return name[: -len(suffix)]
    return name


def _member(qualified: str, entity: str) -> str:
    """Shorten a member reference, unless it was inherited from elsewhere.

    A member access names its target by qualified name, and an entity's own
    members are all prefixed with the entity itself - noise, repeated once per
    rule. An *inherited* member is prefixed with the ancestor that declares it,
    and that prefix is information: it is how you see that a rule on a child
    entity is granting rights over its parent's members.
    """
    if not qualified:
        return "?"
    prefix = entity + "."
    if entity and qualified.startswith(prefix):
        return qualified[len(prefix):]
    return qualified


def _pad(text: str, width: int) -> str:
    return text if len(text) >= width else text + " " * (width - len(text))


# --- the index ---------------------------------------------------------------
#
# Every join in this file needs one of three lookups, and all three are built in
# a single pass over the whole app rather than per module: an entity's
# generalization can point into another module, an association that references
# an entity can be declared in another module, and so can a specialization.
#
# Nothing here writes to a unit. `load_units` hands the parsed dump out by
# reference and keeps it cached for the next call, so a convenience field set on
# an entity would outlive this call and be read by the next one as if the model
# contained it. Every derived value therefore lives in a map beside the data,
# keyed by the entity's own $ID.


class _Index:
    """Entities and associations of the whole app, keyed for the joins."""

    def __init__(self, units: list):
        self.module_of_dm = {}     # domain model $ID -> module name
        self.by_id = {}            # entity $ID -> entity
        self.by_qualified = {}     # lowercased qualified name -> entity
        self.modules = {}          # lowercased module name -> domain model unit
        self.simple = {}           # lowercased entity name -> [entity, ...]
        self.children = {}         # lowercased qualified name -> [entity, ...]
        self._qualified = {}       # entity $ID -> qualified name
        self._module = {}          # entity $ID -> declaring module name
        self.dm_of = {}            # entity $ID -> the $ID of the unit holding it
        # Every association in the app, as (declaring module, association, kind).
        self.associations = []

        module_by_id = {
            u["$ID"]: u.get("name")
            for u in units
            if u.get("$Type") == "Projects$Module" and u.get("$ID")
        }

        for unit in units:
            if unit.get("$Type") != "DomainModels$DomainModel":
                continue
            module = module_by_id.get(unit.get("$ContainerID"))
            if not module:
                continue
            self.module_of_dm[unit.get("$ID")] = module
            self.modules[module.lower()] = unit

            for entity in unit.get("entities") or []:
                name = entity.get("name")
                if not name:
                    continue
                qualified = entity.get("$QualifiedName") or f"{module}.{name}"
                if entity.get("$ID"):
                    self.by_id[entity["$ID"]] = entity
                    self._qualified[entity["$ID"]] = qualified
                    self._module[entity["$ID"]] = module
                    self.dm_of[entity["$ID"]] = unit.get("$ID")
                self.by_qualified[qualified.lower()] = entity
                self.simple.setdefault(name.lower(), []).append(entity)

                parent = (entity.get("generalization") or {}).get("generalization")
                if parent:
                    self.children.setdefault(parent.lower(), []).append(entity)

            for association in unit.get("associations") or []:
                self.associations.append((module, association, "local"))
            for association in unit.get("crossAssociations") or []:
                self.associations.append((module, association, "cross"))

    # -- derived names, held outside the units --

    def qname(self, entity: dict) -> str:
        """The entity's qualified name, without having written it onto it."""
        return (
            self._qualified.get(entity.get("$ID"))
            or entity.get("$QualifiedName")
            or entity.get("name")
            or "?"
        )

    def module(self, entity: dict) -> str:
        declared = self._module.get(entity.get("$ID"))
        if declared:
            return declared
        qualified = self.qname(entity)
        return qualified.rsplit(".", 1)[0] if "." in qualified else ""

    # -- resolving the two ends of an association --

    def side(self, reference: str) -> str:
        """Name the entity an association endpoint refers to.

        A local association names both ends by $ID; a cross-module one names its
        child by qualified name and its parent by $ID. Either form arrives here
        and leaves as a qualified name, with the unresolved id kept visible
        rather than blanked - a dangling reference is a finding, not a gap.
        """
        if not reference:
            return "?"
        if reference in self.by_id:
            return self.qname(self.by_id[reference])
        if "." in reference:
            return reference
        return f"<unresolved {reference}>"

    def entity(self, qualified: str) -> dict:
        return self.by_qualified.get((qualified or "").lower())

    # -- the inheritance chain --

    def ancestry(self, entity: dict) -> tuple[list, dict]:
        """Walk from an entity to the root of its inheritance chain.

        Returns (ancestors nearest-first, the root's NoGeneralization node).
        The second half is the point: `persistable` and the system members are
        stored only at the root, so an entity three levels down has to be asked
        about through its root or the answer is simply absent. A cycle - which
        the modeller cannot draw, but a hand-edited model can hold - terminates
        the walk instead of hanging it.
        """
        chain = []
        seen = {id(entity)}
        current = entity
        while True:
            generalization = current.get("generalization") or {}
            if generalization.get("$Type") == "DomainModels$NoGeneralization":
                return chain, generalization
            parent_name = generalization.get("generalization")
            if not parent_name:
                return chain, {}
            parent = self.entity(parent_name)
            if parent is None:
                chain.append(parent_name)
                return chain, {}
            chain.append(self.qname(parent))
            if id(parent) in seen:
                return chain, {}
            seen.add(id(parent))
            current = parent

    def descendants(self, qualified: str) -> list:
        """Every entity below this one, not just the ones that name it directly.

        The direct count understates what a change to an entity reaches: a
        specialization can itself be specialized, and in a real app the chain
        below one base entity ran two levels deep across thirty modules. Breadth
        first, and `seen` is what keeps a hand-edited cycle from looping here.
        """
        found, queue, seen = [], [qualified.lower()], {qualified.lower()}
        while queue:
            for child in self.children.get(queue.pop(0)) or []:
                key = self.qname(child).lower()
                if key in seen:
                    continue
                seen.add(key)
                found.append(child)
                queue.append(key)
        return found


# --- rendering ---------------------------------------------------------------


class _Renderer:
    """Turns entities and associations into lines. One instance per call."""

    def __init__(self, index: _Index, lang: str, access_rules: bool):
        self.index = index
        self.lang = lang
        self.access_rules = access_rules
        self.suppressed_rules = 0       # rules counted but not printed
        self.dangling = []              # references that resolved to nothing
        self.images = 0                 # entity canvas images dropped

    # -- text --

    def text(self, texts) -> str:
        """Pull a message out of a Texts$Text, in the chosen language.

        Falling back to another translation is deliberate, for the same reason
        as in read_page: an untranslated message still answers "what does this
        say", and printing nothing would state there is no message at all.
        """
        if not isinstance(texts, dict):
            return ""
        translations = texts.get("translations") or []
        for translation in translations:
            if translation.get("languageCode") == self.lang and translation.get("text"):
                return _flat(translation["text"], INLINE_DOC_LIMIT)
        for translation in translations:
            if translation.get("text"):
                return _flat(translation["text"], INLINE_DOC_LIMIT)
        return ""

    # -- attributes --

    def attribute_type(self, attribute: dict) -> str:
        kind = attribute.get("type") or {}
        name = _short(kind.get("$Type"))
        if name == "String" and kind.get("length") is not None:
            # Length 0 is Mendix for "no limit", and printing the 0 reads as the
            # opposite - a string that holds nothing.
            return "String(unlimited)" if kind["length"] == 0 else f"String({kind['length']})"
        if name == "Enumeration":
            return f"Enum<{kind.get('enumeration') or '?'}>"
        if name == "DateTime" and kind.get("localizeDate") is False:
            # Not a detail: an unlocalized DateTime is stored without the
            # session's UTC offset applied, so comparing it against the
            # session's clock in XPath needs a manual correction that nothing
            # else in the model hints at.
            return "DateTime(not localized)"
        return name

    def attribute(self, attribute: dict, indent: str = "  ") -> list:
        name = attribute.get("name") or "?"
        parts = [_pad(self.attribute_type(attribute), 14)]

        value = attribute.get("value") or {}
        kind = value.get("$Type", "")
        if kind.endswith("StoredValue"):
            default = _flat(value.get("defaultValue"), 120)
            if default:
                parts.append(f"= {default}")
        elif kind.endswith("CalculatedValue"):
            passes = ", passes the object" if value.get("passEntity") else ""
            parts.append(f"calculated by {value.get('microflow') or '?'}{passes}")
        elif kind.endswith("OqlViewValue"):
            parts.append(f"from the view's `{value.get('reference') or '?'}`")
        elif kind:
            parts.append(_short(kind))

        if attribute.get("exportLevel") not in (None, "", "Hidden"):
            parts.append(f"exportLevel={attribute['exportLevel']}")

        lines = [f"{indent}attr    {_pad(name, 28)} {' '.join(parts).rstrip()}"]
        documentation = _flat(attribute.get("documentation"), INLINE_DOC_LIMIT)
        if documentation:
            lines.append(f"{indent}        {' ' * 28} doc: {documentation}")
        return lines

    # -- validation, indexes, events --

    def validation(self, rule: dict, entity: str, indent: str) -> str:
        info = rule.get("ruleInfo") or {}
        kind = _short(info.get("$Type")).lower()
        detail = ""
        if kind == "range":
            bounds = []
            if info.get("useMinValue"):
                bounds.append(f">= {info.get('minValue') or info.get('minAttribute') or '?'}")
            if info.get("useMaxValue"):
                bounds.append(f"<= {info.get('maxValue') or info.get('maxAttribute') or '?'}")
            detail = f" {info.get('typeOfRange') or ''} {' and '.join(bounds)}".rstrip()
        elif kind == "regex":
            detail = f" {info.get('regularExpression') or '?'}"
        message = self.text(rule.get("errorMessage"))
        attribute = _member(rule.get("attribute"), entity)
        suffix = f'   "{message}"' if message else ""
        return f"{indent}valid   {_pad(attribute, 28)} {kind}{detail}{suffix}"

    def db_index(self, db_index: dict, entity: dict, indent: str) -> str:
        attributes = {a.get("$ID"): a.get("name") for a in entity.get("attributes") or []}
        columns = []
        for column in db_index.get("attributes") or []:
            reference = column.get("attribute")
            name = attributes.get(reference)
            if name is None:
                # An index column whose attribute is gone. Printed, because an
                # index the model cannot resolve is the kind of thing somebody
                # is looking for, and silence here would read as a valid index.
                name = f"<unresolved {reference or 'null'}>"
                self.dangling.append(f"index column in {self.index.qname(entity)}")
            if column.get("ascending") is False:
                name += " desc"
            if column.get("type") not in (None, "", "Normal"):
                name += f" ({column['type']})"
            columns.append(name)
        offline = "  includeInOffline" if db_index.get("includeInOffline") else ""
        return f"{indent}index   {' + '.join(columns) or '(empty)'}{offline}"

    def event(self, handler: dict, indent: str) -> str:
        moment = (handler.get("moment") or "?").lower()
        event = handler.get("event") or "?"
        flags = []
        if handler.get("passEventObject"):
            flags.append("passes the object")
        if handler.get("raiseErrorOnFalse"):
            flags.append("raises on false")
        suffix = f"   ({', '.join(flags)})" if flags else ""
        return (f"{indent}event   {_pad(moment + ' ' + event, 28)} "
                f"-> {handler.get('microflow') or '?'}{suffix}")

    # -- access rules --

    def rules(self, entity: dict, indent: str) -> list:
        rules = entity.get("accessRules") or []
        if not rules:
            # Said out loud: no rule at all means no role can read the entity,
            # which is a different fact from "the rules were not printed".
            return [f"{indent}rules   none"]

        if not self.access_rules:
            roles = {r for rule in rules for r in rule.get("moduleRoles") or []}
            self.suppressed_rules += len(rules)
            return [
                f"{indent}rules   {len(rules)} access rule(s) over {len(roles)} module "
                "role(s) - not printed; pass access_rules=true"
            ]

        qualified = self.index.qname(entity)
        lines = []
        for rule in rules:
            roles = ", ".join(rule.get("moduleRoles") or []) or "(no role)"
            grants = [verb for verb, key in (("create", "allowCreate"),
                                             ("delete", "allowDelete"))
                      if rule.get(key)]
            default = rule.get("defaultMemberAccessRights") or "?"
            lines.append(
                f"{indent}rule    {roles}"
                f"   [{', '.join(grants) or 'no create/delete'}]"
                f"   other members: {default}"
            )

            constraint = _flat(rule.get("xPathConstraint"), 1200)
            lines.append(f"{indent}        xpath   {constraint or '(none - whole entity)'}")

            caption = _flat(rule.get("xPathConstraintCaption"), INLINE_DOC_LIMIT)
            if caption:
                lines.append(f'{indent}        caption "{caption}"')
            documentation = _flat(rule.get("documentation"), INLINE_DOC_LIMIT)
            if documentation:
                lines.append(f"{indent}        doc     {documentation}")

            # Grouped by right rather than listed one per member: the member
            # list repeats the entity's whole schema once per rule, and it is
            # that repetition - not the rights - that made rules 84.6% of the
            # largest entity in the app.
            grouped = {}
            for access in rule.get("memberAccesses") or []:
                target = access.get("attribute") or access.get("association")
                grouped.setdefault(access.get("accessRights") or "?", []).append(
                    _member(target, qualified)
                )
            for right in sorted(grouped):
                lines.append(
                    f"{indent}        {_pad(right, 9)} {', '.join(sorted(grouped[right]))}"
                )
        return lines

    # -- one entity --

    def entity_body(self, entity: dict, indent: str = "  ") -> list:
        lines = []

        documentation = _flat(entity.get("documentation"), DOC_LIMIT)
        if documentation:
            lines.append(f"{indent}doc     {documentation}")

        source = entity.get("source") or {}
        if source.get("$Type", "").endswith("OqlViewEntitySource"):
            if source.get("sourceDocument"):
                lines.append(f"{indent}view    from {source['sourceDocument']}")
            lines.append(f"{indent}oql     {_flat(source.get('oql'), OQL_LIMIT)}")

        for attribute in entity.get("attributes") or []:
            lines.extend(self.attribute(attribute, indent))
        for db_index in entity.get("indexes") or []:
            lines.append(self.db_index(db_index, entity, indent))
        for rule in entity.get("validationRules") or []:
            lines.append(self.validation(rule, self.index.qname(entity), indent))
        for handler in entity.get("eventHandlers") or []:
            lines.append(self.event(handler, indent))
        lines.extend(self.rules(entity, indent))

        if entity.get("imageData"):
            self.images += 1
        return lines

    def entity_headline(self, entity: dict, qualify: bool = False) -> str:
        """The one line that says what kind of entity this is.

        `persistable` and the system members are read off the root of the
        inheritance chain, never off this entity: they are stored only there,
        and taking the local value classifies every specialization in the app as
        non-persistent. Measured once at six wrong conclusions.
        """
        name = self.index.qname(entity) if qualify else (entity.get("name") or "?")
        chain, root = self.index.ancestry(entity)

        parts = []
        if chain:
            parts.append(": " + " -> ".join(chain))

        if root:
            parts.append("persistable" if root.get("persistable") else "non-persistable")
            members = [label for key, label in _SYSTEM_MEMBERS if root.get(key)]
            if members:
                parts.append("+" + ",".join(members))
        else:
            parts.append("inheritance unresolved - persistence unknown")

        if (entity.get("source") or {}).get("$Type", "").endswith("OqlViewEntitySource"):
            parts.append("[OQL view]")
        if entity.get("exportLevel") not in (None, "", "Hidden"):
            parts.append(f"exportLevel={entity['exportLevel']}")

        if chain and root:
            parts.append(f"(persistence from {chain[-1]})")

        return f"ENTITY  {name}   " + "   ".join(parts)

    # -- associations --

    def association(self, association: dict, kind: str, relative_to: str = None,
                    note_kind: bool = True) -> str:
        parent = self.index.side(association.get("parent"))
        child = self.index.side(association.get("child"))
        for side in (parent, child):
            if side.startswith("<unresolved"):
                self.dangling.append(
                    "association "
                    + str(association.get("$QualifiedName") or association.get("name"))
                )

        # Mendix stores the *many* side as the parent: the parent entity holds
        # the reference and the child is the one being referred to. Owner Both
        # makes it one-to-one, a ReferenceSet makes it many-to-many.
        if (association.get("type") or "") == "ReferenceSet":
            cardinality = "*--*"
        elif association.get("owner") == "Both":
            cardinality = "1--1"
        else:
            cardinality = "*--1"

        behavior = association.get("deleteBehavior") or {}
        rules = []
        for side, key in (("parent", "parentDeleteBehavior"),
                          ("child", "childDeleteBehavior")):
            verb = _DELETE_BEHAVIOR.get(behavior.get(key), behavior.get(key))
            if verb and verb != "keep":
                rules.append(f"delete {side}: {verb}s")
        for key in ("parentErrorMessage", "childErrorMessage"):
            message = self.text(behavior.get(key))
            if message:
                rules.append(f'"{message}"')

        flags = []
        if (association.get("type") == "ReferenceSet"
                and association.get("storageFormat") == "Column"):
            # Many-to-many needs a junction table. A ReferenceSet stored in a
            # column is usually a Reference whose type was changed after the
            # fact, and nothing in Studio Pro flags it.
            flags.append("storageFormat=Column on a ReferenceSet")
        if kind == "cross" and note_kind:
            # Suppressed only where the section heading already said it. In a
            # single-entity read the two kinds are printed side by side and the
            # difference is the point: a cross-module association is a coupling
            # between modules, not just another line.
            flags.append("cross-module")
        source = association.get("source") or {}
        if source.get("reference"):
            flags.append(f"from the view's `{source['reference']}`")

        if relative_to:
            parent = "this" if parent == relative_to else parent
            child = "this" if child == relative_to else child

        name = association.get("name") or "?"
        tail = "   ".join(p for p in (" | ".join(rules), ", ".join(flags)) if p)
        return f"  {_pad(name, 38)} {parent} {cardinality} {child}   {tail}".rstrip()


# --- views -------------------------------------------------------------------


def _module_view(unit: dict, module: str, renderer: _Renderer,
                 index: _Index, notes: list) -> list:
    entities = unit.get("entities") or []
    associations = unit.get("associations") or []
    crossing = unit.get("crossAssociations") or []

    lines = [
        f"{module}   [domain model]",
        f"counts  {len(entities)} entities | {len(associations)} associations | "
        f"{len(crossing)} cross-module associations",
    ]
    documentation = _flat(unit.get("documentation"), DOC_LIMIT)
    if documentation:
        lines.append(f"doc     {documentation}")
    lines.append("")

    for entity in sorted(entities, key=lambda e: (e.get("name") or "").lower()):
        lines.append(renderer.entity_headline(entity))
        lines.extend(renderer.entity_body(entity))
        lines.append("")

    if associations:
        lines.append("ASSOCIATIONS")
        for association in sorted(associations, key=lambda a: (a.get("name") or "").lower()):
            lines.append(renderer.association(association, "local"))
        lines.append("")

    if crossing:
        lines.append("CROSS-MODULE ASSOCIATIONS  (declared here, pointing out)")
        for association in sorted(crossing, key=lambda a: (a.get("name") or "").lower()):
            lines.append(renderer.association(association, "cross", note_kind=False))
        lines.append("")

    # Associations declared in *other* modules that land on an entity here.
    # Nothing in this unit mentions them, and they are as much a dependency of
    # this module as its own are.
    local_ids = {e.get("$ID") for e in entities}
    local_names = {index.qname(e).lower() for e in entities}
    incoming = [
        (owner, association)
        for owner, association, kind in index.associations
        if kind == "cross"
        and (association.get("child") or "").lower() in local_names
        and association.get("parent") not in local_ids
    ]
    if incoming:
        lines.append("INCOMING FROM OTHER MODULES  (declared elsewhere, pointing here)")
        for owner, association in sorted(incoming,
                                         key=lambda p: (p[1].get("name") or "").lower()):
            lines.append(
                renderer.association(association, "cross", note_kind=False)
                + f"   in {owner}"
            )
        lines.append("")

    annotations = [
        _flat(a.get("caption"), INLINE_DOC_LIMIT)
        for a in unit.get("annotations") or []
        if (a.get("caption") or "").strip()
    ]
    if annotations:
        # Canvas sticky notes. They sit next to an entity on the drawing and
        # carry no reference to it, so position is all they were attached by -
        # and position is exactly what does not survive here.
        lines.append("CANVAS NOTES  (not attached to any entity)")
        lines.extend(f'  "{a}"' for a in annotations)
        lines.append("")

    # A module and an entity sharing a name is not an ambiguity in the model -
    # they are different kinds of thing and Mendix never confuses them. It is an
    # ambiguity in this tool's single `name` parameter, and it is only worth a
    # word when the entity that lost the name is somewhere this response does
    # not cover. An entity in THIS module is already printed in full above, so
    # saying "the module won" would warn about nothing: measured on a real app,
    # 14 of the 15 modules that share their name with an entity share it with
    # their own (module Order holding entity Order.Order, the ordinary
    # convention), and a note that fires on the ordinary case teaches the reader
    # to skip notes.
    elsewhere = [
        e for e in index.simple.get(module.lower()) or []
        if index.module(e) != module
    ]
    if elsewhere:
        notes.append(
            f"Read as the module. There is also an entity named '{module}' in "
            f"another module - {', '.join(sorted(index.qname(e) for e in elsewhere))}"
            " - which is not part of this response; ask for it by its qualified name."
        )

    return lines


def _entity_view(entity: dict, renderer: _Renderer, index: _Index) -> list:
    qualified = index.qname(entity)
    lines = [renderer.entity_headline(entity, qualify=True)]

    children = index.children.get(qualified.lower()) or []
    if children:
        deep = len(index.descendants(qualified))
        below = f", {deep} in the whole chain below" if deep > len(children) else ""
        # Grouped by module rather than listed flat: a base entity with a
        # hundred specializations is one unreadable line otherwise, and which
        # modules it reaches is most of what the count is being asked for.
        by_module = {}
        for child in children:
            by_module.setdefault(index.module(child), []).append(
                child.get("name") or index.qname(child)
            )
        lines.append(
            f"subtypes  {len(children)} direct{below}, in {len(by_module)} module(s)"
        )
        for module in sorted(by_module):
            lines.append(f"  {_pad(module, 28)} {', '.join(sorted(by_module[module]))}")

    lines.extend(renderer.entity_body(entity))
    lines.append("")

    outgoing, incoming = [], []
    for owner, association, kind in index.associations:
        if index.side(association.get("parent")) == qualified:
            outgoing.append((owner, association, kind))
        elif index.side(association.get("child")) == qualified:
            incoming.append((owner, association, kind))

    if outgoing:
        lines.append("REFERS TO  (this entity holds the reference)")
        for owner, association, kind in outgoing:
            lines.append(renderer.association(association, kind, qualified))
        lines.append("")

    if incoming:
        lines.append("REFERRED TO BY  (the other entity holds the reference)")
        home = index.module(entity)
        for owner, association, kind in incoming:
            suffix = f"   in {owner}" if owner != home else ""
            lines.append(renderer.association(association, kind, qualified) + suffix)
        lines.append("")

    if not outgoing and not incoming:
        lines.append("No association touches this entity.")
        lines.append("")

    return lines


# --- tool --------------------------------------------------------------------


def read_domain_model(name: str, access_rules: bool = False, lang: str = None,
                      project: str = None) -> dict:
    """Return one module's domain model, or one entity, as text."""
    try:
        units = load_units(project)
        default_lang, codes = languages(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    chosen = lang or default_lang
    if lang and codes and lang not in codes:
        return {
            "success": False,
            "error": (
                f"'{lang}' is not one of this app's languages: {', '.join(codes)}. "
                f"Its default is '{default_lang}'."
            ),
        }

    if not (name or "").strip():
        return {
            "success": False,
            "error": (
                "Give a module name for its whole domain model, or an entity name "
                "for one entity. list_modules has the module names."
            ),
        }
    name = name.strip()

    index = _Index(units)
    renderer = _Renderer(index, chosen, access_rules)
    notes = []

    # A module name is tried first, and it wins a tie on purpose: a module and
    # an entity sharing a name is common (a Customer module holding a Customer
    # entity), the module is the larger answer, and the entity stays reachable
    # by its qualified name. The collision is reported in notes rather than
    # resolved in silence.
    unit = index.modules.get(name.lower())
    if unit is not None:
        module = index.module_of_dm[unit.get("$ID")]
        lines = _module_view(unit, module, renderer, index, notes)
        subject, kind, raw_id = module, "DomainModels$DomainModel", unit.get("$ID")
    else:
        entity = index.entity(name)
        if entity is None:
            candidates = index.simple.get(name.lower()) or []
            if len(candidates) > 1:
                return {
                    "success": False,
                    "error": (
                        f"'{name}' is an entity in {len(candidates)} modules: "
                        + ", ".join(sorted(index.qname(c) for c in candidates))
                        + ". Use the qualified name."
                    ),
                }
            if not candidates:
                return {
                    "success": False,
                    "error": (
                        f"No module and no entity named '{name}'. Entities are not "
                        "top-level units, so search_text will not find one by name "
                        "either - use list_modules for the modules, or "
                        "find_units_by_similar_name for a document."
                    ),
                }
            entity = candidates[0]
        lines = _entity_view(entity, renderer, index)
        subject, kind = index.qname(entity), "DomainModels$Entity"
        raw_id = index.dm_of.get(entity.get("$ID"))

    if renderer.suppressed_rules:
        notes.append(
            f"{renderer.suppressed_rules} access rule(s) were counted but not "
            "printed. They hold the XPath constraints and the per-member rights, "
            "and on the largest entity measured they were 84.6% of its bytes. "
            "Pass access_rules=true for them."
        )
    if renderer.dangling:
        notes.append(
            "Reference(s) that resolved to nothing and print as '<unresolved ...>': "
            + ", ".join(sorted(set(renderer.dangling)))
        )
    if renderer.images:
        notes.append(f"{renderer.images} entity canvas image(s) were not printed.")

    text = "\n".join(lines).rstrip() + "\n"
    if access_rules and kind.endswith("DomainModel") and len(text) > BULK_WARNING:
        # Asked for and delivered in full - but a module-wide rule dump is the
        # one call here that can still cost a large fraction of a context
        # window, because each rule lists the entity's whole member set again.
        notes.append(
            f"This is {len(text):,} characters, most of it per-member rights "
            f"repeated once per rule. read_domain_model('{subject}.<Entity>', "
            "access_rules=true) answers the same question for one entity."
        )
    if notes and raw_id:
        # Not find_unit_by_name: a domain model unit has no name to find it by,
        # and an entity is not a unit at all. The $ID is the only way in, and
        # this tool is where it is already resolved.
        held = "holding it" if kind.endswith("Entity") else ""
        notes.append(
            f"For the exact model, use find_unit_by_id('{raw_id}') - the raw "
            f"domain model unit {held}".rstrip() + "."
        )

    result = {
        "success": True,
        "name": subject,
        "type": kind,
        "language": chosen,
        "text": text,
    }
    if notes:
        result["notes"] = notes
    return result


TOOL_DEFINITION = {
    "name": "read_domain_model",
    "description": (
        "Read a module's domain model, or a single entity, as text: attributes with "
        "their types and defaults, the inheritance chain with the persistence that "
        "is stored only at its root, validation rules, indexes, event handlers, and "
        "every association with both ends named instead of referenced by UUID. Use "
        "this to answer what the data model is. A domain model has no name, so "
        "find_unit_by_name cannot reach one; for the exact model, 'notes' carries "
        "its $ID for find_unit_by_id."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "A module name for its whole domain model ('MyModule'), or an "
                    "entity for just that entity ('MyModule.Order'). A name that is "
                    "both is read as the module."
                ),
            },
            "access_rules": {
                "type": "boolean",
                "description": (
                    "Print each access rule with its XPath constraint and per-member "
                    "rights. Off by default: they are the bulk of a domain model. "
                    "The count is always reported."
                ),
            },
            "lang": {
                "type": "string",
                "description": (
                    "Language code for validation and delete-behavior messages, e.g. "
                    "'en_US'. Defaults to the app's own default language."
                ),
            },
        },
        "required": ["name"],
    },
}


if __name__ == "__main__":
    print(read_domain_model("MyModule")["text"])
