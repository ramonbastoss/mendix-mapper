"""Read a microflow as linearized text instead of as the model's raw JSON.

The dump is the Studio Pro file format: it describes a *drawing*. Every object
carries its pixel position and size, every edge carries a bezier curve with two
control vectors and two connection indexes, and the only thing tying the two
together is a UUID. None of that is the logic, and on a medium microflow it is
roughly 95% of the bytes.

So this resolves the graph here - walks the sequence flows, numbers the steps,
indents the branches - and prints what is left: what each activity does, what
the splits ask, which microflows get called with which arguments, and what the
validation messages actually say. The UUIDs can go because the order they
encoded is now explicit in the text.

Two things do not survive, and both are signposted in the output rather than
hidden: a node reached from more than one place prints as `goto <n>` the second
time, and the $IDs themselves are gone. Either one is a reason to fall back to
find_unit_by_name, which still returns the raw unit under the same name.
"""

from tools._utils import load_units, unit_names

MICROFLOW_TYPES = (
    "Microflows$Microflow",
    "Microflows$Nanoflow",
    "Microflows$Rule",
)

_INDENT = "  "


# --- small readers ---------------------------------------------------------

def _text_of(texts: dict) -> str:
    """Pull the string out of a Texts$Text, which buries it one list deep.

    Multi-language apps hold one translation per language. Any of them answers
    'what does this say', so the first non-empty one wins; when they disagree it
    is a translation question, not a logic question, and this is not the tool
    for it.
    """
    if not isinstance(texts, dict):
        return ""
    for translation in texts.get("translations") or []:
        if translation.get("text"):
            return translation["text"]
    return ""


def _template(template: dict) -> str:
    """Render a StringTemplate or TextTemplate with its {1}, {2} placeholders filled.

    The arguments are microflow expressions held in a sibling list, so the text
    and the values it interpolates are never next to each other in the JSON.
    They are here.
    """
    if not isinstance(template, dict):
        return ""

    raw = template.get("text")
    text = raw if isinstance(raw, str) else _text_of(raw)

    for i, argument in enumerate(template.get("arguments") or [], start=1):
        expression = _flat(argument.get("expression") or "")
        text = text.replace("{%d}" % i, "{%s}" % expression)

    return _flat(text)


# A single expression longer than this is a payload, not logic. Measured: one
# marketplace microflow holds 521 KB of seed data in one string literal, which
# on its own is larger than every real microflow in the app put together.
# The cut is announced inline, so no separate bookkeeping is needed to keep it
# honest: the reader sees exactly where it happened and how much is missing.
VALUE_LIMIT = 1200


def _flat(value: str) -> str:
    """Collapse a multi-line expression onto one line, and cap a runaway literal.

    Mendix expressions are routinely written across several lines inside the
    editor. Keeping those newlines would break the indentation that carries the
    branch structure here, and the structure is worth more than the formatting.
    """
    if not isinstance(value, str):
        return ""
    flat = " ".join(value.split())
    if len(flat) > VALUE_LIMIT:
        return flat[:VALUE_LIMIT] + f"...(+{len(flat) - VALUE_LIMIT} chars)"
    return flat


def _var(name: str) -> str:
    return "$" + name if name else "?"


def _range(range_: dict) -> str:
    """'first' or '' for a constant range, the expressions for a custom one."""
    if not isinstance(range_, dict):
        return ""
    if range_.get("$Type") == "Microflows$ConstantRange":
        return "first" if range_.get("singleObject") else ""
    offset = _flat(range_.get("offsetExpression") or "")
    limit = _flat(range_.get("limitExpression") or "")
    if offset or limit:
        return f"range(offset={offset or '0'}, limit={limit or 'all'})"
    return ""


def _sort(sort_item_list: dict) -> str:
    items = (sort_item_list or {}).get("items") or []
    parts = [
        f"{i.get('attributePath', '?')} {str(i.get('direction', '')).lower()}".strip()
        for i in items
    ]
    return ("sort by " + ", ".join(parts)) if parts else ""


# A Java or JavaScript action parameter does not always hold an expression.
# Depending on the parameter's declared type the value is an entity name, a
# microflow name or a nanoflow name instead - each under its own key. Reading
# only `argument` rendered 2,267 of 4,890 call arguments as `empty` in the app
# this was measured against, which is not a smaller answer, it is a wrong one.
_PARAMETER_VALUE_KEYS = ("argument", "entity", "microflow", "nanoflow")


def _parameter_value(value) -> str:
    if isinstance(value, dict):
        for key in _PARAMETER_VALUE_KEYS:
            if value.get(key):
                return value[key]
        return "empty"
    return value or "empty"


def _mappings(mappings: list, argument_key: str = "argument") -> str:
    """Render call arguments as Param=expression, with the module prefix dropped.

    The `parameter` field is fully qualified ('Module.Microflow.ParamName'),
    which triples the width of the line and repeats the callee's own name that
    is already right there. Only the last segment carries information.
    """
    parts = []
    for mapping in mappings or []:
        parameter = (mapping.get("parameter") or "").split(".")[-1]
        value = _parameter_value(mapping.get(argument_key))
        parts.append(f"{parameter}={_flat(value)}")
    return ", ".join(parts)


def _data_type(data_type: dict) -> str:
    """'DataTypes$ObjectType' + entity -> 'Module.Entity'; otherwise 'string', 'void'."""
    if not isinstance(data_type, dict):
        return "?"
    kind = (data_type.get("$Type") or "").replace("DataTypes$", "").replace("Type", "")
    entity = data_type.get("entity")
    if entity:
        return f"{entity}" if kind == "Object" else f"{kind}<{entity}>"
    enumeration = data_type.get("enumeration")
    if enumeration:
        return enumeration
    return kind.lower() or "?"


# --- activity renderers ----------------------------------------------------
#
# One function per action $Type, each returning a single line. A type with no
# entry here still prints (see _render_action): the tool degrades to naming the
# type and its non-default fields rather than silently dropping a step.

def _a_microflow_call(a: dict) -> str:
    call = a.get("microflowCall") or {}
    args = _mappings(call.get("parameterMappings"))
    return f"call {call.get('microflow', '?')}({args})"


def _a_nanoflow_call(a: dict) -> str:
    call = a.get("nanoflowCall") or {}
    args = _mappings(call.get("parameterMappings"))
    return f"call-nano {call.get('nanoflow', '?')}({args})"


def _a_java_call(a: dict) -> str:
    args = _mappings(a.get("parameterMappings"), argument_key="parameterValue")
    return f"java {a.get('javaAction', '?')}({args})"


def _a_javascript_call(a: dict) -> str:
    args = _mappings(a.get("parameterMappings"), argument_key="parameterValue")
    return f"js {a.get('javaScriptAction', '?')}({args})"


def _a_retrieve(a: dict) -> str:
    source = a.get("retrieveSource") or {}
    kind = source.get("$Type")

    if kind == "Microflows$AssociationRetrieveSource":
        start = _var(source.get("startVariableName"))
        return f"retrieve {start}/{source.get('association', '?')}"

    if kind == "Microflows$DatabaseRetrieveSource":
        parts = [f"retrieve db {source.get('entity', '?')}"]
        xpath = _flat(source.get("xPathConstraint") or "")
        if xpath:
            parts.append(xpath)
        for extra in (_range(source.get("range")), _sort(source.get("sortItemList"))):
            if extra:
                parts.append(extra)
        return " ".join(parts)

    return f"retrieve {kind or '?'}"


def _members(items: list) -> str:
    parts = []
    for item in items or []:
        member = item.get("attribute") or item.get("association") or "?"
        member = member.split(".")[-1]
        change = item.get("type")
        operator = {"Set": "=", "Add": "+=", "Remove": "-="}.get(change, f" {change} ")
        parts.append(f"{member}{operator}{_flat(item.get('value') or 'empty')}")
    return ", ".join(parts)


def _commit_note(a: dict) -> str:
    """Only say something when it is not the default. 'commit=No' is the default."""
    notes = []
    commit = a.get("commit")
    if commit and commit != "No":
        notes.append(f"commit={commit}")
    if a.get("refreshInClient"):
        notes.append("refresh")
    return ("   # " + ", ".join(notes)) if notes else ""


def _a_change_object(a: dict) -> str:
    target = _var(a.get("changeVariableName"))
    members = _members(a.get("items"))
    if not members:
        # A change activity that changes nothing is how you refresh an object in
        # the client, or commit it. Calling that 'change' reads like a defect.
        if a.get("refreshInClient"):
            return f"refresh {target}" + (
                f" (commit={a['commit']})" if a.get("commit", "No") != "No" else ""
            )
        return f"change {target}: no members{_commit_note(a)}"
    return f"change {target}: {members}{_commit_note(a)}"


def _a_create_object(a: dict) -> str:
    members = _members(a.get("items"))
    body = f": {members}" if members else ""
    return f"create {a.get('entity', '?')}{body}{_commit_note(a)}"


def _a_change_variable(a: dict) -> str:
    return f"set {_var(a.get('changeVariableName'))} = {_flat(a.get('value') or '')}"


def _a_create_variable(a: dict) -> str:
    kind = _data_type(a.get("variableType"))
    return f"var {_var(a.get('variableName'))}: {kind} = {_flat(a.get('initialValue') or '')}"


def _a_validation(a: dict) -> str:
    member = a.get("attribute") or a.get("association") or ""
    target = _var(a.get("objectVariableName"))
    if member:
        target += "/" + member.split(".")[-1]
    return f'validation {target}: "{_template(a.get("feedbackTemplate"))}"'


def _a_show_message(a: dict) -> str:
    kind = (a.get("type") or "message").lower()
    blocking = " blocking" if a.get("blocking") else ""
    return f'{kind}{blocking}: "{_template(a.get("template"))}"'


def _a_log(a: dict) -> str:
    level = (a.get("level") or "").lower()
    node = a.get("node") or ""
    node = f" [{node}]" if node else ""
    return f'log {level}{node}: "{_template(a.get("messageTemplate"))}"'


def _a_commit(a: dict) -> str:
    events = "" if a.get("withEvents", True) else " without-events"
    refresh = " refresh" if a.get("refreshInClient") else ""
    return f"commit {_var(a.get('commitVariableName'))}{events}{refresh}"


def _a_delete(a: dict) -> str:
    return f"delete {_var(a.get('deleteVariableName'))}"


def _a_rollback(a: dict) -> str:
    return f"rollback {_var(a.get('rollbackVariableName'))}"


def _a_show_page(a: dict) -> str:
    settings = a.get("pageSettings") or {}
    args = _mappings(settings.get("parameterMappings"))
    return f"page {settings.get('page', '?')}({args})"


def _a_close_form(a: dict) -> str:
    count = a.get("numberOfPagesToClose")
    return "close page" + (f" x{count}" if count and count != 1 else "")


def _a_create_list(a: dict) -> str:
    return f"list of {a.get('entity', '?')}"


def _a_change_list(a: dict) -> str:
    return (f"list {_var(a.get('changeVariableName'))} "
            f"{(a.get('type') or '?').lower()} {_flat(a.get('value') or '')}")


def _a_list_operation(a: dict) -> str:
    operation = a.get("operation") or {}
    kind = (operation.get("$Type") or "?").replace("Microflows$", "").lower()
    target = _var(operation.get("listVariableName"))

    detail = (operation.get("expression")
              or operation.get("secondListOrObjectVariableName")
              or operation.get("attributePath")
              or "")
    detail = _flat(detail) if isinstance(detail, str) else ""
    return f"list {kind} {target}" + (f" [{detail}]" if detail else "")


def _a_aggregate(a: dict) -> str:
    function = (a.get("aggregateFunction") or "?").lower()
    if a.get("useExpression"):
        over = _flat(a.get("expression") or "")
    else:
        over = (a.get("attribute") or "").split(".")[-1]
    source = _var(a.get("inputListVariableName"))
    return f"{function} {source}" + (f"/{over}" if over else "")


def _a_cast(a: dict) -> str:
    # The target entity is not on the action; it is on the inheritance split or
    # the parameter that consumes it. Naming the output is all that is honest.
    return "cast"


def _a_download(a: dict) -> str:
    return f"download {_var(a.get('fileDocumentVariableName'))}"


def _a_import_xml(a: dict) -> str:
    """Import mapping call. Despite the name it also covers JSON - contentType says which."""
    call = ((a.get("resultHandling") or {}).get("importMappingCall")) or {}
    content = (call.get("contentType") or "xml").lower()
    source = _var(a.get("xmlDocumentVariableName"))
    return f"import {content} {source} via {call.get('mapping', '?')}"


def _a_export_xml(a: dict) -> str:
    call = ((a.get("resultHandling") or {}).get("exportMappingCall")) or {}
    return f"export via {call.get('mapping', '?')}"


def _a_rest_call(a: dict) -> str:
    http = a.get("httpConfiguration") or {}
    method = (http.get("newHttpMethod") or http.get("httpMethod") or "?").upper()
    location = _template(http.get("customLocationTemplate")) or http.get("customLocation") or "?"
    return f"rest {method} {location}"


_ACTIONS = {
    "Microflows$MicroflowCallAction": _a_microflow_call,
    "Microflows$NanoflowCallAction": _a_nanoflow_call,
    "Microflows$JavaActionCallAction": _a_java_call,
    "Microflows$JavaScriptActionCallAction": _a_javascript_call,
    "Microflows$RetrieveAction": _a_retrieve,
    "Microflows$ChangeObjectAction": _a_change_object,
    "Microflows$CreateObjectAction": _a_create_object,
    "Microflows$ChangeVariableAction": _a_change_variable,
    "Microflows$CreateVariableAction": _a_create_variable,
    "Microflows$ValidationFeedbackAction": _a_validation,
    "Microflows$ShowMessageAction": _a_show_message,
    "Microflows$LogMessageAction": _a_log,
    "Microflows$CommitAction": _a_commit,
    "Microflows$DeleteAction": _a_delete,
    "Microflows$RollbackAction": _a_rollback,
    "Microflows$ShowPageAction": _a_show_page,
    "Microflows$ShowHomePageAction": lambda a: "home page",
    "Microflows$CloseFormAction": _a_close_form,
    "Microflows$CreateListAction": _a_create_list,
    "Microflows$ChangeListAction": _a_change_list,
    "Microflows$ListOperationAction": _a_list_operation,
    "Microflows$AggregateListAction": _a_aggregate,
    "Microflows$CastAction": _a_cast,
    "Microflows$DownloadFileAction": _a_download,
    "Microflows$RestCallAction": _a_rest_call,
    "Microflows$ImportXmlAction": _a_import_xml,
    "Microflows$ExportXmlAction": _a_export_xml,
}

# Present on every action and never worth printing: either a default, or
# already rendered as part of the line above.
_ACTION_NOISE = {
    "$ID", "$Type", "errorHandlingType", "outputVariableName",
    "useReturnVariable", "queueSettings",
}


def _render_action(action: dict, unknown: set) -> str:
    kind = action.get("$Type") or "?"
    renderer = _ACTIONS.get(kind)
    if renderer:
        return renderer(action)

    # No renderer: print the type and whatever it carries that is not empty, so
    # a step the tool has never seen still shows up as a step.
    unknown.add(kind)
    fields = []
    for key, value in action.items():
        if key in _ACTION_NOISE or value in (None, "", [], {}, False):
            continue
        fields.append(f"{key}={_flat(str(value))[:120]}")
    short = kind.replace("Microflows$", "").replace("Action", "")
    return f"{short}({', '.join(fields)})"


# --- graph -----------------------------------------------------------------

def _collect(objects: list, into: dict):
    """Index every object by $ID, descending into loop bodies.

    A LoopedActivity nests its children in its own objectCollection but the
    sequence flows that connect them live in the *unit's* single flows array -
    the graph is flat even though the objects are not. Both facts have to be
    true at once for the walk below to work, which is why the index is built
    recursively and the edges are not.
    """
    for obj in objects or []:
        oid = obj.get("$ID")
        if not oid:
            continue
        into[oid] = obj
        nested = (obj.get("objectCollection") or {}).get("objects")
        if nested:
            _collect(nested, into)


def _case_label(flow: dict) -> str:
    values = [
        case.get("value") for case in flow.get("caseValues") or []
        if case.get("$Type") != "Microflows$NoCase" and case.get("value")
    ]
    return ", ".join(values)


def _branch_order(label: str) -> tuple:
    """true before false, named cases alphabetically, the empty default last.

    Reading order should match the order the conditions are written in, and an
    unlabelled edge is the fall-through - it belongs at the bottom.
    """
    if label == "true":
        return (0, "")
    if label == "false":
        return (1, "")
    if not label:
        return (3, "")
    return (2, label)


# A branch this short that goes nowhere else is a bail-out, not half of a
# fork: the validation that fails, the guard that returns early. Six steps is
# wide enough for 'set a flag, warn the user, leave' and narrow enough that no
# real alternative path fits in it.
GUARD_LIMIT = 6


class _Walk:
    """Turn the flow graph into an ordered list of items, then into text.

    Items rather than finished lines, because two of the things this has to get
    right are only knowable once the whole walk is over: a step's number has to
    match the order it is finally *read* in, which guard extraction reorders,
    and a `goto` has to name a step that may not have been printed yet when the
    jump itself was.
    """

    def __init__(self, objects: dict, flows: list):
        self.objects = objects
        self.items = []
        self.visited = set()
        self.unknown = set()
        self.gotos = 0

        self.out = {}
        for flow in flows or []:
            if flow.get("$Type") != "Microflows$SequenceFlow":
                continue
            origin, destination = flow.get("origin"), flow.get("destination")
            if not origin or not destination:
                continue
            self.out.setdefault(origin, []).append(flow)

    # -- item construction --

    def _emit(self, depth: int, text: str, oid: str = None):
        self.items.append({"kind": "step", "depth": depth, "text": text, "oid": oid})

    def _label(self, depth: int, text: str):
        self.items.append({"kind": "label", "depth": depth, "text": text})

    def _merge_marker(self, oid: str):
        # A merge is not a step and prints nothing, but a `goto` can land on it.
        # The marker lets it adopt the number of whatever step follows it in the
        # final order.
        self.items.append({"kind": "merge", "oid": oid})

    def _goto(self, depth: int, target: str):
        self.gotos += 1
        self.items.append({"kind": "goto", "depth": depth, "target": target})

    # -- traversal --

    def walk(self, oid: str, depth: int):
        while oid:
            if oid in self.visited:
                # Second arrival at a node already walked: a merge, or a loop
                # back-edge. Point at the step instead of printing it twice.
                self._goto(depth, oid)
                return

            obj = self.objects.get(oid)
            if obj is None:
                return

            self.visited.add(oid)
            oid = self._render(obj, oid, depth)

    def _reach(self, oid: str) -> set:
        """Every node reachable from `oid`, ignoring nodes already walked."""
        seen, stack = set(), [oid]
        while stack:
            current = stack.pop()
            if current in seen or current in self.visited:
                continue
            seen.add(current)
            for flow in self.out.get(current, []):
                stack.append(flow["destination"])
        return seen

    def _render(self, obj: dict, oid: str, depth: int):
        """Print one object and return the next $ID to continue with, or None."""
        kind = obj.get("$Type")
        successors = self.out.get(oid, [])
        normal = [f for f in successors if not f.get("isErrorHandler")]
        handlers = [f for f in successors if f.get("isErrorHandler")]

        if kind == "Microflows$StartEvent":
            return normal[0]["destination"] if normal else None

        if kind == "Microflows$EndEvent":
            value = _flat(obj.get("returnValue") or "")
            self._emit(depth, "end" + (f" -> {value}" if value else ""), oid)
            return None

        if kind == "Microflows$ErrorEvent":
            self._emit(depth, "raise error", oid)
            return None

        if kind in ("Microflows$ContinueEvent", "Microflows$BreakEvent"):
            word = "continue" if kind.endswith("ContinueEvent") else "break"
            self._emit(depth, word, oid)
            return None

        if kind == "Microflows$ExclusiveMerge":
            self._merge_marker(oid)
            return normal[0]["destination"] if normal else None

        if kind in ("Microflows$ExclusiveSplit", "Microflows$InheritanceSplit"):
            self._split(obj, oid, depth, normal)
            return None

        if kind == "Microflows$LoopedActivity":
            self._loop(obj, oid, depth, normal, handlers)
            return None

        if kind == "Microflows$ActionActivity":
            return self._activity(obj, oid, depth, normal, handlers)

        if kind == "Microflows$Annotation":
            return None  # canvas sticky note, not a step

        self.unknown.add(kind or "?")
        self._emit(depth, (kind or "?").replace("Microflows$", ""), oid)
        return normal[0]["destination"] if normal else None

    def _split(self, obj: dict, oid: str, depth: int, successors: list):
        condition = obj.get("splitCondition") or {}
        caption = _flat(obj.get("caption") or "")
        caption = f' "{caption}"' if caption else ""

        if obj.get("$Type") == "Microflows$InheritanceSplit":
            test = f"type of {_var(obj.get('splitVariableName'))}"
        elif condition.get("$Type") == "Microflows$RuleSplitCondition":
            rule = (condition.get("ruleCall") or {}).get("rule", "?")
            test = f"rule {rule}"
        else:
            test = _flat(condition.get("expression") or "")

        ordered = sorted(successors, key=lambda f: _branch_order(_case_label(f)))
        guard, main = self._guard_split(ordered)

        if guard is not None:
            self._emit(depth, f"split{caption}  {test}"
                              f"   (falls through on {_case_label(main) or 'the other case'})",
                       oid)

            # Walk the main line first so it owns the shared tail and the guard
            # ends in a `goto` back into it - and only then move its items below
            # the guard block, so the page reads split, bail-out, carry on.
            # Numbering happens over the final order, so the steps still count
            # upwards as they are read.
            anchor = len(self.items)
            # The main line resumes at the split's own depth. Without this, a
            # chain of validations - which is what most VAL_ microflows are -
            # indents once per check and ends up 70 levels deep, where the
            # whitespace is twice the size of the logic.
            self.walk(main["destination"], depth)
            main_items = self.items[anchor:]
            del self.items[anchor:]

            self._branch(guard, depth)
            self.items.extend(main_items)
            return

        self._emit(depth, f"split{caption}  {test}", oid)
        for flow in ordered:
            self._branch(flow, depth)

    def _branch(self, flow: dict, depth: int):
        self._label(depth + 1, f"{_case_label(flow) or 'otherwise'}:")
        self.walk(flow["destination"], depth + 2)

    def _guard_split(self, ordered: list):
        """Split a two-way fork into (bail-out branch, main branch), or (None, None).

        What separates a guard from a genuine fork is not the condition, it is
        what each side owns: a guard's branch leads only to its own few steps
        and then rejoins, while the other side carries the rest of the flow. So
        the test is on the nodes each branch reaches that the *other* one does
        not - the shared tail cancels out, which is what makes it measurable at
        all.
        """
        if len(ordered) != 2:
            return None, None

        first, second = ordered
        reach_first = self._reach(first["destination"])
        reach_second = self._reach(second["destination"])
        only_first = len(reach_first - reach_second)
        only_second = len(reach_second - reach_first)

        # A guard that rejoins owns a short detour and nothing else, so the main
        # branch's exclusive set is empty - it goes straight to what both sides
        # share. A guard that returns early owns its few steps and an end event,
        # and then the main branch's set is the large one. Both shapes are the
        # same statement about ownership, counted from opposite ends.
        if only_first == 0 and 0 < only_second <= GUARD_LIMIT:
            guard, main, main_reach = second, first, reach_first
        elif only_second == 0 and 0 < only_first <= GUARD_LIMIT:
            guard, main, main_reach = first, second, reach_second
        elif only_first <= GUARD_LIMIT < only_second:
            guard, main, main_reach = first, second, reach_second
        elif only_second <= GUARD_LIMIT < only_first:
            guard, main, main_reach = second, first, reach_first
        else:
            return None, None

        # Flattening only earns its keep when there is a long main line to keep
        # flat. On a fork where both sides are a couple of steps, spending a
        # sentence to say which one falls through costs more than the two levels
        # of indentation it saves.
        if len(main_reach) <= GUARD_LIMIT:
            return None, None

        return guard, main

    def _loop(self, obj: dict, oid: str, depth: int, successors: list, handlers: list):
        source = obj.get("loopSource") or {}
        if source.get("$Type") == "Microflows$WhileLoopCondition":
            header = f"while {_flat(source.get('expression') or '')}"
        else:
            header = (f"for {_var(source.get('variableName'))} "
                      f"in {_var(source.get('listVariableName'))}")

        self._emit(depth, header, oid)

        body = (obj.get("objectCollection") or {}).get("objects") or []
        entry = self._loop_entry(body)
        if entry:
            self.walk(entry, depth + 1)

        self._handlers(handlers, obj, depth)

        if successors:
            self.walk(successors[0]["destination"], depth)

    def _loop_entry(self, body: list) -> str:
        """The first step of a loop body.

        A loop has no StartEvent inside it, so the entry is found structurally:
        the body object that no other body object flows into.
        """
        ids = {o.get("$ID") for o in body}
        targets = {
            flow["destination"]
            for oid in ids
            for flow in self.out.get(oid, [])
            if flow.get("destination") in ids
        }
        for obj in body:
            if obj.get("$ID") not in targets and obj.get("$Type") != "Microflows$Annotation":
                return obj.get("$ID")
        return None

    def _activity(self, obj: dict, oid: str, depth: int, successors: list, handlers: list):
        action = obj.get("action") or {}
        line = _render_action(action, self.unknown)

        if action.get("useReturnVariable", True) and action.get("outputVariableName"):
            line += f" -> {_var(action['outputVariableName'])}"

        caption = _flat(obj.get("caption") or "")
        if caption and not obj.get("autoGenerateCaption") and caption != "Activity":
            line += f'   # "{caption}"'

        self._emit(depth, line, oid)
        self._handlers(handlers, obj, depth)

        return successors[0]["destination"] if successors else None

    def _handlers(self, handlers: list, obj: dict, depth: int):
        if not handlers:
            return
        action = obj.get("action") or {}
        kind = action.get("errorHandlingType") or obj.get("errorHandlingType") or "?"
        for flow in handlers:
            self._label(depth + 1, f"onerror({kind}):")
            self.walk(flow["destination"], depth + 2)

    # -- rendering --

    def render(self) -> list:
        """Number the steps in reading order and resolve every goto to a number."""
        number_of = {}
        step = 0
        waiting = []  # merges that have no number of their own yet

        for item in self.items:
            if item["kind"] == "merge":
                waiting.append(item["oid"])
                continue
            if item["kind"] != "step":
                continue
            step += 1
            item["number"] = step
            if item["oid"]:
                number_of[item["oid"]] = step
            for oid in waiting:
                number_of[oid] = step
            waiting = []

        lines = []
        for item in self.items:
            if item["kind"] == "merge":
                continue
            indent = _INDENT * item["depth"]
            if item["kind"] == "step":
                lines.append(f"{indent}{item['number']:3d} {item['text']}")
            elif item["kind"] == "goto":
                # A target that never got a number is a jump into something the
                # walk did not print. Say so rather than invent a step.
                target = number_of.get(item["target"])
                lines.append(f"{indent}    goto {target}" if target
                             else f"{indent}    goto (unresolved)")
            else:
                lines.append(f"{indent}{item['text']}")
        return lines


# --- header ----------------------------------------------------------------

def _header(unit: dict, objects: dict) -> list:
    name = unit.get("$QualifiedName") or unit.get("name") or "?"
    returns = _data_type(unit.get("microflowReturnType"))
    kind = (unit.get("$Type") or "").replace("Microflows$", "").lower()

    lines = [f"{name} -> {returns}" + (f"   [{kind}]" if kind != "microflow" else "")]

    parameters = [
        o for o in objects.values()
        if o.get("$Type") == "Microflows$MicroflowParameterObject"
    ]
    if parameters:
        rendered = " | ".join(
            f"{p.get('name', '?')}: {_data_type(p.get('variableType'))}"
            for p in parameters
        )
        lines.append(f"params  {rendered}")

    access = []
    if unit.get("applyEntityAccess") is not None:
        access.append(f"applyEntityAccess={str(unit['applyEntityAccess']).lower()}")
    if unit.get("exportLevel"):
        access.append(f"exportLevel={unit['exportLevel']}")
    if unit.get("excluded"):
        access.append("EXCLUDED FROM BUILD")
    if access:
        lines.append("access  " + " | ".join(access))

    roles = unit.get("allowedModuleRoles") or []
    if roles:
        lines.append(f"roles   {', '.join(roles)}")

    documentation = (unit.get("documentation") or "").strip()
    if documentation:
        lines.append(f"doc     {_flat(documentation)}")

    notes = [
        _flat(o.get("caption") or "")
        for o in objects.values()
        if o.get("$Type") == "Microflows$Annotation" and _flat(o.get("caption") or "")
    ]
    for note in notes:
        lines.append(f"note    {note}")

    return lines


# --- tool ------------------------------------------------------------------

def read_microflow(name: str, project: str = None) -> dict:
    """Return one microflow, nanoflow or rule as linearized text."""
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    found = [u for u in units if name in unit_names(u)]
    if not found:
        return {"success": False, "error": f"No unit found with the exact name: '{name}'"}

    # A qualified name is not unique across unit types: a scheduled event takes
    # the name of the microflow it runs, and a page template the name of its
    # page. Since this tool only reads flows, narrowing by type resolves those
    # without bothering the caller - a simple name matching two modules is a
    # real ambiguity and still does.
    flows = [u for u in found if u.get("$Type") in MICROFLOW_TYPES]

    if not flows:
        kinds = ", ".join(sorted({u.get("$Type", "?") for u in found}))
        return {
            "success": False,
            "error": (
                f"'{name}' is a {kinds}, which has no sequence flow to linearize. "
                "Use find_unit_by_name for it."
            ),
        }

    if len(flows) > 1:
        qualified = sorted({u.get("$QualifiedName") or u.get("name") for u in flows})
        return {
            "success": False,
            "error": (
                f"'{name}' matches {len(flows)} flows: {', '.join(qualified)}. "
                "Use the qualified name."
            ),
        }

    unit = flows[0]

    objects = {}
    _collect((unit.get("objectCollection") or {}).get("objects"), objects)

    walk = _Walk(objects, unit.get("flows"))
    start = next(
        (oid for oid, o in objects.items() if o.get("$Type") == "Microflows$StartEvent"),
        None,
    )
    if start:
        walk.walk(start, 0)

    lines = _header(unit, objects) + [""] + walk.render()

    # Anything the walk never reached. Usually dead canvas left behind by an
    # edit; occasionally a sign that the walk itself lost the thread, which is
    # why it is reported rather than discarded.
    walkable = {
        oid for oid, o in objects.items()
        if o.get("$Type") not in ("Microflows$Annotation",
                                  "Microflows$MicroflowParameterObject",
                                  "Microflows$StartEvent")
    }
    orphans = len(walkable - walk.visited)

    notes = []
    if walk.gotos:
        notes.append(
            f"{walk.gotos} edge(s) printed as 'goto <n>': the graph merges or loops "
            "there and does not linearize."
        )
    if walk.unknown:
        notes.append(
            "Printed generically, no dedicated renderer: "
            + ", ".join(sorted(walk.unknown))
        )
    if orphans:
        notes.append(f"{orphans} object(s) unreachable from the start event.")
    if notes:
        notes.append(f"For the exact model, use find_unit_by_name('{name}').")

    result = {
        "success": True,
        "name": unit.get("$QualifiedName") or unit.get("name"),
        "type": unit.get("$Type"),
        "text": "\n".join(lines),
    }
    if notes:
        result["notes"] = notes
    return result


TOOL_DEFINITION = {
    "name": "read_microflow",
    "description": (
        "Read one microflow, nanoflow or rule as linearized text: numbered steps, "
        "indented branches, calls with their arguments, and validation messages "
        "resolved. Use this to answer what a flow does. For the exact model - $IDs, "
        "layout, a diff - use find_unit_by_name on the same name."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Qualified name, e.g. 'MyModule.ACT_Save'.",
            },
        },
        "required": ["name"],
    },
}


if __name__ == "__main__":
    print(read_microflow("MyModule.ACT_Save")["text"])
