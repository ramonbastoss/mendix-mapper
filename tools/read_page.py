"""Read a page as a linearized widget tree instead of as the model's raw JSON.

Same problem as read_microflow, one order of magnitude worse. A page in the dump
is a *drawing* of a form: every widget carries its styling slots, its tab index,
its render mode and a UUID, and the median page spends 52.8% of its characters on
UUIDs, `$` keys and fields left at their default. 217 of this app's 564 pages do
not fit in a context window at all, and 5 exceed the spill cap and come back as a
file path.

So this walks the widget tree and prints what a page actually is: what it takes
as parameters, which entity each data container reads and through what, the text
of every caption and validation message, which microflow or page each button
calls with which arguments, and the condition that decides whether a widget is
visible. Three joins the model leaves for the reader are resolved here, and that
is where most of the saving comes from:

  * a caption is a list of translations, one per language, with no indication of
    which one the users read. The app's default language answers it (`lang=`
    overrides);
  * a custom widget embeds its entire property *schema* - every property's
    caption, description and 25-field value type - next to the two or three
    values actually set, which reference the schema by UUID. Measured on this
    app: 37% of all custom-widget bytes are schema, and custom widgets dominate
    the page family. Resolving the join lets the schema go;
  * `Pages$NoClientAction` - "this widget does nothing on click" - is 105,053
    objects and 6.3% of every page in the app. It prints as nothing.

Two things do not survive, both signposted in the output rather than hidden: a
container that holds one child and carries no class, condition or action is
dropped and its child printed in its place, and pixel geometry is gone. Either
one is a reason to fall back to find_unit_by_name, which still returns the raw
unit under the same name.
"""

import re

from tools._utils import languages, load_units, unit_names

# `$Name` as written inside a microflow expression or a dynamic class. Used to
# find the expressions that read another widget's selection by name.
_VARIABLE_TOKEN = re.compile(r"\$(\w+)")

PAGE_TYPES = (
    "Pages$Page",
    "Pages$Snippet",
    "Pages$PageTemplate",
    "Pages$BuildingBlock",
    "Pages$Layout",
)

_INDENT = "  "

# A single value longer than this is a payload, not a page: a base64 preview
# thumbnail, a pasted block of HTML. Page templates and building blocks carry
# `imageData` worth 8.5% and 12.6% of their own bytes, and no question about a
# page is answered by it. The cut is announced inline, so the reader sees where
# it happened and how much is missing.
VALUE_LIMIT = 400


def _flat(value, limit: int = VALUE_LIMIT) -> str:
    """Collapse a value onto one line and cap a runaway literal."""
    if value is None or isinstance(value, bool):
        return ""
    flat = " ".join(str(value).split())
    if len(flat) > limit:
        return flat[:limit] + f"...(+{len(flat) - limit} chars)"
    return flat


def _short(kind: str) -> str:
    """'Pages$ActionButton' -> 'ActionButton'."""
    return (kind or "?").split("$")[-1]


def _last(qualified: str) -> str:
    return (qualified or "").split(".")[-1]


# --- the reader ------------------------------------------------------------
#
# Language and the enclosing data context both change what a given node should
# print, and both are properties of where you are in the tree rather than of the
# node itself. They travel together in one small immutable object: `within()`
# returns a new reader rather than mutating this one, so a sibling subtree can
# never inherit a context that belongs to its neighbour.

class _Reader:
    """Resolves captions, member references and variables for one walk."""

    def __init__(self, lang: str, entity: str = None):
        self.lang = lang
        self.entity = entity

    def within(self, entity: str) -> "_Reader":
        return _Reader(self.lang, entity) if entity else self

    # -- text --

    def text(self, texts) -> str:
        """Pull the caption out of a Texts$Text, in the chosen language.

        Falling back to any other translation is deliberate: a caption that was
        never translated into the chosen language still answers 'what does this
        say', and printing nothing would read as a widget with no caption at
        all, which is a different fact about the page.
        """
        if not isinstance(texts, dict):
            return ""
        translations = texts.get("translations") or []
        for translation in translations:
            if translation.get("languageCode") == self.lang and translation.get("text"):
                return _flat(translation["text"])
        for translation in translations:
            if translation.get("text"):
                return _flat(translation["text"])
        return ""

    def template(self, template) -> str:
        """Render a ClientTemplate with its {1}, {2} placeholders filled.

        The placeholders' values are attribute references held in a sibling
        list, so the text and the data it interpolates are never next to each
        other in the JSON. They are here.
        """
        if not isinstance(template, dict):
            return ""
        if template.get("$Type") == "Texts$Text":
            return self.text(template)

        text = self.text(template.get("template"))
        for i, parameter in enumerate(template.get("parameters") or [], start=1):
            text = text.replace("{%d}" % i, "{%s}" % self.value_of(parameter))

        if not text:
            # An empty template with a fallback is how a caption says "use this
            # when the data is empty", and then the fallback IS the caption.
            return self.text(template.get("fallback"))
        return text

    # -- references --

    def value_of(self, holder) -> str:
        """An attribute reference, an expression or a variable, whichever is set.

        ClientTemplateParameter, MicroflowParameterMapping and the custom-widget
        value object all carry the same choice under the same key names, so they
        all read the same way.
        """
        if not isinstance(holder, dict):
            return ""
        if holder.get("attributeRef"):
            return self.attribute(holder["attributeRef"])
        if holder.get("expression"):
            return _flat(holder["expression"])
        if holder.get("entityRef"):
            return self.entity_ref(holder["entityRef"])
        if holder.get("sourceVariable"):
            return self.variable(holder["sourceVariable"])
        return ""

    def attribute(self, ref: dict) -> str:
        """'Module.Entity.Attribute', shortened against the enclosing context.

        Inside a data view over Module.Entity, every input on the form repeats
        that prefix; the entity is already on the container's line two steps up.
        Dropping it there - and only there - is the difference between `Street`
        and `Sales.CustomerAddress.Street` on sixty consecutive lines.
        """
        if not isinstance(ref, dict):
            return ""
        attribute = ref.get("attribute") or ""
        path = self.entity_ref(ref.get("entityRef"))

        if not path and self.entity and attribute.startswith(self.entity + "."):
            attribute = attribute[len(self.entity) + 1:]

        return f"{path}/{_last(attribute)}" if path else attribute

    def entity_ref(self, ref) -> str:
        """A direct reference prints nothing; an indirect one prints its path.

        A DirectEntityRef only ever restates the entity the container already
        named, so on a member it is noise. The association path of an indirect
        one is the whole point: it is how the widget reaches data it was not
        given.
        """
        if not isinstance(ref, dict):
            return ""
        if ref.get("$Type") == "DomainModels$DirectEntityRef":
            return ""
        steps = [
            _last(step.get("association") or "?")
            for step in ref.get("steps") or []
        ]
        return "/".join(steps)

    def target_entity(self, ref) -> str:
        """The entity a reference lands on - its last step, or itself."""
        if not isinstance(ref, dict):
            return ""
        steps = ref.get("steps") or []
        if steps:
            return steps[-1].get("destinationEntity") or ""
        return ref.get("entity") or ""

    def variable(self, variable) -> str:
        """A PageVariable: '$Parameter', or '@grid1' for a widget's selection."""
        if not isinstance(variable, dict):
            return ""
        for key in ("pageParameter", "snippetParameter", "localVariable"):
            if variable.get(key):
                return "$" + variable[key]
        if variable.get("widget"):
            # Not a variable at all: the current selection of another widget on
            # the page. A different thing, so it gets a different sigil.
            selection = "@" + variable["widget"]
            return selection + "(all pages)" if variable.get("useAllPages") else selection
        return ""


# --- call arguments --------------------------------------------------------

def _mappings(mappings: list, r: _Reader) -> str:
    """Render call arguments as Param=value, with the callee's prefix dropped.

    `parameter` is fully qualified ('Module.Microflow.ParamName') and repeats the
    callee's own name that is already on the same line. Only the last segment
    carries information.
    """
    parts = []
    for mapping in mappings or []:
        name = _last(mapping.get("parameter") or "?")
        # Page, microflow and snippet mappings call it `variable`; the widget
        # value object and the client template call the same thing
        # `sourceVariable`. Both have to be read or every argument on the page
        # prints as `empty`, which is not a smaller answer, it is a wrong one.
        value = (r.variable(mapping.get("variable"))
                 or r.value_of(mapping)
                 or _flat(mapping.get("argument"))
                 or "empty")
        parts.append(f"{name}={value}")
    return ", ".join(parts)


# --- client actions --------------------------------------------------------
#
# One function per action $Type, each returning a single phrase that goes after
# the '->' on a widget's line. An action with no entry here still prints (see
# _action): the tool degrades to naming the type rather than dropping the fact
# that the widget does something.

def _microflow_settings(settings: dict, r: _Reader) -> str:
    settings = settings or {}
    line = f"call {settings.get('microflow', '?')}({_mappings(settings.get('parameterMappings'), r)})"

    confirmation = settings.get("confirmationInfo") or {}
    question = r.text(confirmation.get("question"))
    if question:
        line += f' confirm "{question}"'
    if settings.get("asynchronous"):
        line += " async"
    if settings.get("formValidations") not in (None, "", "All"):
        line += f" validations={settings['formValidations']}"
    return line


def _page_settings(settings: dict, r: _Reader) -> str:
    settings = settings or {}
    line = f"page {settings.get('page', '?')}({_mappings(settings.get('parameterMappings'), r)})"
    override = r.text(settings.get("titleOverride"))
    if override:
        line += f' title "{override}"'
    return line


def _close_suffix(action: dict) -> str:
    return ", close page" if action.get("closePage") else ""


def _ac_microflow(a, r):
    return _microflow_settings(a.get("microflowSettings"), r)


def _ac_nanoflow(a, r):
    return f"call-nano {a.get('nanoflow', '?')}({_mappings(a.get('parameterMappings'), r)})"


def _ac_page(a, r):
    line = _page_settings(a.get("pageSettings"), r)
    for specialization in a.get("pagesForSpecializations") or []:
        entity = specialization.get("entity") or "?"
        line += f" | if {entity}: {_page_settings(specialization.get('pageSettings'), r)}"
    return line


def _ac_create(a, r):
    entity = r.target_entity(a.get("entityRef")) or "?"
    return f"create {entity} -> {_page_settings(a.get('pageSettings'), r)}"


def _ac_link(a, r):
    address = a.get("address") or {}
    target = r.value_of(address) or _flat(address.get("value")) or "?"
    return f"link {(a.get('linkType') or '').lower()} {target}".strip()


def _ac_delete(a, r):
    target = r.variable(a.get("sourceVariable"))
    return f"delete {target}".strip() + _close_suffix(a)


_ACTIONS = {
    "Pages$NoClientAction": lambda a, r: "",
    "Pages$MicroflowClientAction": _ac_microflow,
    "Pages$CallNanoflowClientAction": _ac_nanoflow,
    "Pages$PageClientAction": _ac_page,
    "Pages$CreateObjectClientAction": _ac_create,
    "Pages$OpenLinkClientAction": _ac_link,
    "Pages$DeleteClientAction": _ac_delete,
    "Pages$ClosePageClientAction": lambda a, r: "close page",
    "Pages$SaveChangesClientAction": lambda a, r: "save" + _close_suffix(a),
    "Pages$CancelChangesClientAction": lambda a, r: "cancel" + _close_suffix(a),
    "Pages$SignOutClientAction": lambda a, r: "sign out",
    "Pages$SignInClientAction": lambda a, r: "sign in",
    "Pages$SyncClientAction": lambda a, r: "sync",
    "Pages$OpenUserTaskClientAction": lambda a, r: "open user task",
    "Pages$CallWorkflowClientAction": lambda a, r: f"workflow {a.get('workflow', '?')}",
}


def _action(action, r: _Reader, unknown: set = None) -> str:
    if not isinstance(action, dict):
        return ""
    kind = action.get("$Type")
    renderer = _ACTIONS.get(kind)
    if renderer:
        return renderer(action, r)
    if unknown is not None:
        unknown.add(kind or "?")
    return _short(kind).replace("ClientAction", "").lower()


# --- data sources ----------------------------------------------------------

def _sort(sort_bar) -> str:
    items = (sort_bar or {}).get("sortItems") or []
    parts = [
        f"{_last((i.get('attributeRef') or {}).get('attribute') or '?')} "
        f"{str(i.get('sortDirection', '')).lower()}".strip()
        for i in items
    ]
    return ("sort by " + ", ".join(parts)) if parts else ""


def _database_source(source: dict, r: _Reader) -> str:
    """An XPath source, shared by the grid, the list view and custom widgets."""
    entity = r.target_entity(source.get("entityRef"))
    variable = r.variable(source.get("sourceVariable"))
    path = r.entity_ref(source.get("entityRef"))

    if variable and path:
        head = f"{variable}/{path}"
    elif variable:
        head = variable
    elif entity:
        head = f"db {entity}"
    else:
        head = "db ?"

    if variable and entity:
        head += f": {entity}"

    parts = [head]
    xpath = _flat(source.get("xPathConstraint"))
    if xpath:
        parts.append(f"[{xpath}]")
    order = _sort(source.get("sortBar"))
    if order:
        parts.append(order)
    return " ".join(parts)


def _object_source(source: dict, r: _Reader) -> str:
    """A data view or association source: a variable, an entity, or both."""
    variable = r.variable(source.get("sourceVariable"))
    path = r.entity_ref(source.get("entityRef"))
    entity = r.target_entity(source.get("entityRef"))

    head = f"{variable}/{path}" if variable and path else (variable or "")
    if head and entity:
        return f"{head}: {entity}"
    return head or entity or "?"


_SOURCES = {
    "Pages$DataViewSource": _object_source,
    "Pages$AssociationSource": _object_source,
    "Pages$MicroflowSource": lambda s, r: _microflow_settings(s.get("microflowSettings"), r),
    "Pages$NanoflowSource": lambda s, r: (
        f"call-nano {s.get('nanoflow', '?')}({_mappings(s.get('parameterMappings'), r)})"
    ),
    "Pages$ListViewXPathSource": _database_source,
    "Pages$GridXPathSource": _database_source,
    "CustomWidgets$CustomWidgetXPathSource": _database_source,
    "Pages$ListenTargetSource": lambda s, r: f"listen @{s.get('listenTarget', '?')}",
    "Pages$NavigationSource": lambda s, r: f"navigation {s.get('navigationProfile', '?')}",
    "Pages$MenuDocumentSource": lambda s, r: f"menu {s.get('menu', '?')}",
}


def _source(source, r: _Reader, unknown: set = None) -> str:
    if not isinstance(source, dict):
        return ""
    kind = source.get("$Type")
    renderer = _SOURCES.get(kind)
    if renderer:
        return renderer(source, r)
    if unknown is not None:
        unknown.add(kind or "?")
    return _short(kind)


def _source_entity(node: dict, r: _Reader) -> str:
    """The entity a container puts its children in, for shortening their members."""
    source = node.get("dataSource")
    if not isinstance(source, dict):
        return ""
    entity = r.target_entity(source.get("entityRef"))
    if entity:
        return entity
    # A microflow or nanoflow source names its entity nowhere on the page - it
    # is the flow's return type, which lives in another unit. Nothing to shorten
    # against, so children keep their full prefixes.
    return ""


# --- suffixes --------------------------------------------------------------
#
# Styling, visibility and identity are cross-cutting: they appear on almost
# every widget type and are rendered the same way on all of them, so they are
# appended by the walker rather than repeated in forty renderers.

def _condition(settings, r: _Reader) -> str:
    """A visibility or editability rule, as one expression.

    The attribute form - 'show when this enumeration is X' - is a list of
    conditions, each naming a value and whether it shows. Written out as an
    expression it is one line instead of six objects, and it reads like the
    question the reader is asking: when is this visible.
    """
    if not isinstance(settings, dict):
        return ""

    expression = _flat(settings.get("expression"))
    if expression:
        return expression

    attribute = settings.get("attribute")
    if attribute:
        variable = r.variable(settings.get("sourceVariable"))
        target = (f"{variable}/{_last(attribute)}" if variable
                  else r.attribute({"attribute": attribute}))
        shown = [
            c.get("attributeValue")
            for c in settings.get("conditions") or []
            if c.get("editableVisible") and c.get("attributeValue")
        ]
        if shown:
            return f"{target} in ({', '.join(shown)})"
        return f"{target} (no value shows)"

    roles = settings.get("moduleRoles") or []
    if roles:
        # Kept fully qualified: two modules can both define 'Administrador', and
        # which module's role grants this is the whole question.
        return "role in (" + ", ".join(roles) + ")"
    return ""


def _appearance(node: dict) -> str:
    """Only the styling that was actually set: class, inline style, design properties.

    41,850 appearance objects in this app and 18,746 of them set a class, which
    is the only handle between a page and the stylesheet. The other three fields
    of the object are empty almost everywhere, and the object itself never says
    anything.
    """
    appearance = node.get("appearance")
    if not isinstance(appearance, dict):
        return ""

    parts = []
    for name in (appearance.get("class") or "").split():
        parts.append("." + name)

    dynamic = _flat(appearance.get("dynamicClasses"))
    if dynamic:
        parts.append(f".{{{dynamic}}}")

    style = _flat(appearance.get("style"))
    if style:
        parts.append(f"style({style})")

    design = _design_properties(appearance.get("designProperties"))
    if design:
        parts.append(design)

    return " ".join(parts)


def _design_properties(properties) -> str:
    """Atlas design properties, flattened to key=value.

    A compound property nests a second list of key/value pairs one level down
    (`Spacing` -> `margin-right=S`); the nesting carries no meaning the pair does
    not, so it is flattened out.
    """
    pairs = []
    for item in properties or []:
        key = item.get("key") or "?"
        value = item.get("value") or {}
        kind = value.get("$Type")

        if kind == "Pages$OptionDesignPropertyValue":
            pairs.append(f"{key}={value.get('option', '?')}")
        elif kind == "Pages$ToggleDesignPropertyValue":
            pairs.append(key)
        elif kind == "Pages$CompoundDesignPropertyValue":
            inner = _design_properties(value.get("properties"))
            pairs.append(f"{key}({inner})" if inner else key)
        else:
            pairs.append(key)
    return ", ".join(pairs)


def _validation(node: dict, r: _Reader) -> str:
    validation = node.get("validation")
    if not isinstance(validation, dict):
        return ""
    message = r.text(validation.get("message"))
    expression = _flat(validation.get("expression"))
    if expression:
        return f'require {expression} else "{message}"'
    return f'require "{message}"' if message else ""


# --- widget renderers ------------------------------------------------------
#
# One function per widget $Type, each returning the widget's own line - without
# the suffixes, which the walker appends. Returning None means "this node says
# nothing": the walker drops the line and prints the children in its place. That
# is how 14,624 layout grids, rows and columns and 8,325 div containers stop
# costing a line each for the fact that a form has boxes in it.

def _w_container(w, r):
    """A div, a grid, a row: structure only. Transparent unless it does something.

    The walker only calls this once the node has been found to carry no class,
    no condition and no action, so by then there is provably nothing to say.
    """
    return None


def _w_column(w, r):
    # A weight of 1-12 is the column's share of the 12-column grid and is worth
    # printing. The two negative sentinels - auto-size and fill - are the
    # defaults on 6,388 of this app's 6,977 columns and say nothing.
    weight = w.get("weight") or 0
    return f"col {weight}" if weight > 0 else "col"


def _w_text(w, r):
    content = r.template(w.get("content"))
    mode = w.get("renderMode")
    label = "text" if mode in (None, "", "Text") else f"text:{str(mode).lower()}"
    return f'{label} "{content}"'


def _w_label(w, r):
    return f'label "{r.text(w.get("caption"))}"'


def _w_title(w, r):
    return "title"


def _icon(icon) -> str:
    if not isinstance(icon, dict):
        return ""
    return icon.get("image") or icon.get("code") or ""


def _w_button(w, r):
    caption = r.template(w.get("caption"))
    icon = _icon(w.get("icon"))

    line = f'button "{caption}"' if caption else (f"button [{icon}]" if icon else "button")
    if caption and icon:
        line += f" [{icon}]"

    tooltip = r.text(w.get("tooltip"))
    if tooltip and tooltip != caption:
        line += f' tip "{tooltip}"'

    action = _action(w.get("action"), r)
    if action:
        line += " -> " + action
    return line


def _w_data_view(w, r):
    line = f"dataview {_source(w.get('dataSource'), r)}"
    if w.get("editability") not in (None, "", "Always"):
        line += f" {str(w['editability']).lower()}"
    empty = r.text(w.get("noEntityMessage"))
    if empty:
        line += f' empty "{empty}"'
    return line


def _w_list_view(w, r):
    line = f"listview {_source(w.get('dataSource'), r)}"
    action = _action(w.get("clickAction"), r)
    if action:
        line += " -> " + action
    if w.get("pageSize"):
        line += f" page={w['pageSize']}"
    return line


def _w_grid(w, r):
    line = f"{_short(w.get('$Type')).lower()} {_source(w.get('dataSource'), r)}"
    if w.get("selectionMode") not in (None, "", "None"):
        line += f" select={str(w['selectionMode']).lower()}"
    return line


def _w_input(w, r):
    """Every editable widget: text box, date picker, check box, drop down.

    They differ in the editor and in almost nothing the reader of a page needs:
    each one binds a member, may carry a label and a validation message, and may
    be conditionally read-only. The type name is kept as the verb so the
    difference is still visible.
    """
    kind = _short(w.get("$Type")).lower()
    member = r.attribute(w.get("attributeRef")) or r.variable(w.get("sourceVariable"))
    line = f"{kind} {member}".strip()

    label = r.template(w.get("labelTemplate"))
    if label:
        line += f' "{label}"'

    placeholder = r.template(w.get("placeholderTemplate"))
    if placeholder:
        line += f' hint "{placeholder}"'

    if w.get("isPasswordBox"):
        line += " password"
    if w.get("inputMask"):
        line += f" mask={_flat(w['inputMask'])}"
    if w.get("editable") not in (None, "", "Always"):
        line += f" {str(w['editable']).lower()}"

    for key, verb in (("onChangeAction", "onchange"),
                      ("onEnterKeyPressAction", "onenter"),
                      ("onLeaveAction", "onleave")):
        action = _action(w.get(key), r)
        if action:
            line += f" {verb}-> {action}"

    requirement = _validation(w, r)
    if requirement:
        line += " " + requirement
    return line


def _w_reference_selector(w, r):
    line = _w_input(w, r)
    for key, verb in (("selectPageSettings", "select"), ("gotoPageSettings", "goto")):
        settings = w.get(key)
        if isinstance(settings, dict) and settings.get("page"):
            line += f" {verb}-> {_page_settings(settings, r)}"
    source = _source(w.get("selectorSource"), r)
    if source:
        line += f" from {source}"
    return line


def _w_file(w, r):
    line = f"file {str(w.get('type') or '').lower()}".strip()
    label = r.template(w.get("labelTemplate"))
    if label:
        line += f' "{label}"'
    if w.get("allowedExtensions"):
        line += f" ext={_flat(w['allowedExtensions'])}"
    if w.get("maxFileSize"):
        line += f" max={w['maxFileSize']}MB"
    return line


def _w_image(w, r):
    line = f"image {w.get('image') or '?'}"
    alt = r.template(w.get("alternativeText"))
    if alt:
        line += f' alt "{alt}"'
    action = _action(w.get("clickAction"), r)
    if action:
        line += " -> " + action
    return line


def _w_custom_widget(w, r):
    """The widget's identity. Its configured values are printed by the walker.

    `widgetId` rather than the display name because it is the identity you can
    look anything else up by - the package in the marketplace, the folder in
    javascript source, the other pages using it.
    """
    line = f"widget {(w.get('type') or {}).get('widgetId') or '?'}"
    label = r.template(w.get("labelTemplate"))
    if label:
        line += f' "{label}"'
    if w.get("editable") not in (None, "", "Always"):
        line += f" {str(w['editable']).lower()}"
    return line


def _w_snippet_call(w, r):
    call = w.get("snippetCall") or {}
    return f"snippet {call.get('snippet', '?')}({_mappings(call.get('parameterMappings'), r)})"


def _w_tabs(w, r):
    line = "tabs"
    if w.get("defaultPage"):
        line += " default=?"
    member = r.attribute(w.get("activePageAttributeRef"))
    if member:
        line += f" active={member}"
    action = _action(w.get("activePageOnChangeAction"), r)
    if action:
        line += " onchange-> " + action
    return line


def _w_tab(w, r):
    line = f'tab "{r.text(w.get("caption"))}"'
    badge = r.template(w.get("badge"))
    if badge:
        line += f' badge "{badge}"'
    return line


def _w_grid_column(w, r):
    line = f"column {r.attribute(w.get('attributeRef'))}".strip()
    caption = r.text(w.get("caption"))
    if caption:
        line += f' "{caption}"'
    if w.get("aggregateFunction") not in (None, "", "None"):
        line += f" {str(w['aggregateFunction']).lower()}"
    if w.get("editable"):
        line += " editable"
    return line


def _w_placeholder(w, r):
    return f"placeholder {w.get('name') or '?'}"


def _w_menu(w, r):
    return f"{_short(w.get('$Type')).lower()} {_source(w.get('menuSource'), r)}"


def _w_search_field(w, r):
    member = r.attribute(w.get("attributeRef"))
    line = f"search {member}".strip()
    caption = r.text(w.get("caption"))
    if caption:
        line += f' "{caption}"'
    if w.get("operator"):
        line += f" {str(w['operator']).lower()}"
    if w.get("defaultValue"):
        line += f" = {_flat(w['defaultValue'])}"
    return line


def _w_layout_call(w, r):
    """The page's layout. Named in the header, so the call itself is transparent."""
    return None


def _w_layout_argument(w, r):
    """One placeholder of the layout being filled.

    Labelled only when the page fills more than one: with a single argument the
    name repeats the layout already named in the header, and 626 of this app's
    630 layout arguments are the only one on their page.
    """
    return None


_WIDGETS = {
    "Pages$DivContainer": _w_container,
    "Pages$LayoutGrid": _w_container,
    "Pages$LayoutGridRow": _w_container,
    "Pages$LayoutGridColumn": _w_column,
    "Pages$TableRow": _w_container,
    "Pages$TableColumn": _w_container,
    "Pages$Table": lambda w, r: "table",
    "Pages$TableCell": lambda w, r: "cell" + (" header" if w.get("isHeader") else ""),
    "Pages$ScrollContainer": lambda w, r: "scroll",
    "Pages$ScrollContainerRegion": _w_container,
    "Pages$Header": lambda w, r: "header",
    "Pages$GroupBox": lambda w, r: f'group "{r.template(w.get("caption"))}"',
    "Pages$WebLayoutContent": _w_container,
    "Pages$NativeLayoutContent": _w_container,
    "Pages$LayoutCall": _w_layout_call,
    "Pages$LayoutCallArgument": _w_layout_argument,

    "Pages$DynamicText": _w_text,
    "Pages$Label": _w_label,
    "Pages$Title": _w_title,
    "Pages$ActionButton": _w_button,
    "Pages$SelectButton": _w_button,
    "Pages$GridActionButton": _w_button,
    "Pages$GridSearchButton": _w_button,
    "Pages$GridNewButton": _w_button,
    "Pages$SidebarToggleButton": _w_button,
    "Pages$StaticImageViewer": _w_image,
    "Pages$DynamicImageViewer": _w_image,

    "Pages$DataView": _w_data_view,
    "Pages$ListView": _w_list_view,
    "Pages$DataGrid": _w_grid,
    "Pages$TemplateGrid": _w_grid,
    "Pages$GridColumn": _w_grid_column,
    "Pages$GridControlBar": _w_container,
    "Pages$SearchBar": _w_container,
    "Pages$ComparisonSearchField": _w_search_field,
    "Pages$DropDownSearchField": _w_search_field,
    "Pages$ListViewTemplate": _w_container,
    "Pages$ListViewSearch": _w_container,

    "Pages$TextBox": _w_input,
    "Pages$TextArea": _w_input,
    "Pages$DatePicker": _w_input,
    "Pages$CheckBox": _w_input,
    "Pages$RadioButtonGroup": _w_input,
    "Pages$DropDown": _w_input,
    "Pages$ReferenceSelector": _w_reference_selector,
    "Pages$InputReferenceSetSelector": _w_reference_selector,
    "Pages$FileManager": _w_file,

    "CustomWidgets$CustomWidget": _w_custom_widget,

    "Pages$TabContainer": _w_tabs,
    "Pages$TabPage": _w_tab,
    "Pages$SnippetCallWidget": _w_snippet_call,
    "Pages$Placeholder": _w_placeholder,
    "Pages$TemplatePlaceholder": _w_placeholder,
    "Pages$MenuBar": _w_menu,
    "Pages$SimpleMenuBar": _w_menu,
    "Pages$NavigationTree": _w_menu,
    "Pages$NavigationList": _w_container,
    "Pages$NavigationListItem": lambda w, r: f'item "{r.template(w.get("caption"))}"',
}

# Rendered elsewhere on the widget's own line, or never worth printing. The
# generic fallback consults this so that a widget type nobody has written a
# renderer for still prints its distinguishing fields and not its boilerplate.
_WIDGET_NOISE = {
    "$ID", "$Type", "$QualifiedName", "$ContainerID", "$ContainerProperty",
    "name", "appearance", "conditionalVisibilitySettings",
    "conditionalEditabilitySettings", "tabIndex", "widgets", "footerWidgets",
    "items", "columns", "rows", "cells", "tabPages", "templates", "content",
    "arguments", "leftWidgets", "rightWidgets", "sidebarWidgets",
    "center", "top", "left", "right", "bottom", "rightHeaderPlaceholder",
    "caption", "tooltip", "icon", "action", "dataSource", "validation",
    "labelTemplate", "placeholderTemplate", "screenReaderLabel",
    "attributeRef", "sourceVariable", "formattingInfo", "renderMode",
    "renderType", "readOnlyStyle", "buttonStyle", "ariaRole", "nativeTextStyle",
    "widthUnit", "heightUnit", "responsive", "editable", "width", "height",
    "weight", "tabletWeight", "phoneWeight", "previewWidth", "imageData",
    "verticalAlignment", "horizontalAlignment", "spacingBetweenColumns",
    "onChangeAction", "onEnterAction", "onLeaveAction", "onEnterKeyPressAction",
    "onClickAction", "keyboardType", "autocomplete", "autocompletePurpose",
    "submitBehaviour", "submitOnInputDelay", "maxLengthCode", "numberOfLines",
    "labelPosition", "nativeRenderMode", "clickAction", "pullDownAction",
    "scrollBehavior", "layoutMode", "widthMode", "alignment", "sizeMode",
    "size", "toggleMode", "isHeader", "topRowIndex", "leftColumnIndex",
    "type", "object",
}


def _w_generic(w, r, unknown: set) -> str:
    """A widget type with no renderer: name it and print what it carries.

    The point is that an unrecognised widget still shows up as a widget, in the
    right place in the tree, with whatever distinguishes it from its neighbours
    - rather than silently not being on the page at all.
    """
    unknown.add(w.get("$Type") or "?")
    fields = []
    for key, value in w.items():
        if key in _WIDGET_NOISE or value in (None, "", [], {}, False, 0):
            continue
        if value is True:
            # A flag that is set says everything by being named. Sending it
            # through _flat would render it as the empty string and drop it.
            fields.append(key)
            continue
        if isinstance(value, dict):
            fields.append(f"{key}={_short(value.get('$Type'))}")
            continue
        if isinstance(value, list):
            fields.append(f"{key}[{len(value)}]")
            continue
        rendered = _flat(value, 80)
        if rendered:
            fields.append(f"{key}={rendered}")
    return f"{_short(w.get('$Type'))}({', '.join(fields)})"


# --- custom widgets --------------------------------------------------------
#
# The single biggest win on a page, and the only place this tool does real work
# the model refuses to do.
#
# A custom widget instance embeds the widget's whole property *schema* - for
# every property its key, caption, description and a 25-field value type, plus
# the same again for every nested object type - and then, separately, the values
# that were actually set, each pointing back into that schema by UUID. The schema
# is identical for every instance of the same widget in the app and is not part
# of the page. Resolving the join prints `key = value` and lets all of it go:
# measured at 37% of all custom-widget bytes in this app, before counting the
# default-valued properties that then drop out too.

def _property_types(node, into: dict):
    """Index every WidgetPropertyType and WidgetObjectType in a widget's schema.

    Both are keyed by $ID because both get referenced that way: a property names
    its own type, and a nested object names the object type whose property list
    describes it.
    """
    if isinstance(node, dict):
        if node.get("$Type") in ("CustomWidgets$WidgetPropertyType",
                                 "CustomWidgets$WidgetObjectType"):
            into[node.get("$ID")] = node
        for value in node.values():
            _property_types(value, into)
    elif isinstance(node, list):
        for value in node:
            _property_types(value, into)


# Every field of a WidgetValue that can hold the value the modeller typed. They
# are mutually exclusive in practice - which one is used depends on the
# property's declared type - so reading them in order is how you get the value
# without having to interpret the type first.
_VALUE_KEYS = ("primitiveValue", "expression", "page", "microflow", "nanoflow",
               "image", "xPathConstraint")


def _widget_value(value: dict, r: _Reader, unknown: set) -> tuple:
    """One custom-widget property value, as (text, child widgets, child objects).

    Returns empty text for a property left at its default, which is most of
    them: a widget declares every property it supports and a page sets two.
    """
    if not isinstance(value, dict):
        return "", [], []

    for key in _VALUE_KEYS:
        if value.get(key):
            return _flat(value[key]), [], []

    member = r.attribute(value.get("attributeRef"))
    if member:
        return member, [], []

    reference = r.entity_ref(value.get("entityRef")) or r.target_entity(value.get("entityRef"))
    if reference:
        return reference, [], []

    template = r.template(value.get("textTemplate")) or r.text(value.get("translatableValue"))
    if template:
        return f'"{template}"', [], []

    icon = _icon(value.get("icon"))
    if icon:
        return icon, [], []

    action = _action(value.get("action"), r, unknown)
    if action:
        return action, [], []

    source = _source(value.get("dataSource"), r, unknown)
    if source:
        return source, [], []

    variable = r.variable(value.get("sourceVariable"))
    if variable:
        return variable, [], []

    if value.get("selection") not in (None, "", "None"):
        return str(value["selection"]), [], []

    return "", value.get("widgets") or [], value.get("objects") or []


def _widget_properties(obj: dict, schema: dict, r: _Reader, unknown: set) -> list:
    """Resolve one WidgetObject's properties against the schema, in schema order.

    Schema order rather than stored order because the schema is the order the
    properties appear in the widget's own editor, which is the order whoever
    configured the page saw them in.
    """
    resolved = []
    by_type = {}
    for prop in obj.get("properties") or []:
        by_type.setdefault(prop.get("type"), []).append(prop)

    object_type = schema.get(obj.get("type")) or {}
    ordered = [
        declared.get("$ID")
        for declared in object_type.get("propertyTypes") or []
    ]
    # Anything the schema does not mention still gets printed, at the end: an
    # unresolved join is a fact about the model, not a licence to drop a value.
    ordered += [key for key in by_type if key not in ordered]

    for type_id in ordered:
        declared = schema.get(type_id) or {}
        key = declared.get("key") or "?"
        default = (declared.get("valueType") or {}).get("defaultValue")
        for prop in by_type.get(type_id, []):
            text, widgets, objects = _widget_value(prop.get("value"), r, unknown)
            # A property still holding the value the widget itself declares as
            # its default was never configured - Studio Pro stores every
            # property whether or not anyone touched it. The schema is the only
            # place that default is written down, and it is indexed here
            # already, so the comparison is free.
            if text and default not in (None, "") and text == str(default):
                continue
            if text or widgets or objects:
                resolved.append((key, text, widgets, objects))
    return resolved


# --- the walk --------------------------------------------------------------
#
# Where a node keeps its children, in reading order. A whitelist rather than a
# blacklist on purpose: descending into everything would walk straight into a
# custom widget's schema and into the appearance objects, which is most of the
# bytes this tool exists to not print. The label is printed above the block when
# the key alone would not say which part of the parent it fills.

_CHILD_KEYS = (
    ("content", None),
    ("layoutCall", None),
    ("arguments", None),
    ("rows", None),
    ("columns", None),
    ("cells", None),
    ("tabPages", None),
    ("controlBar", None),
    ("searchBar", None),
    ("templates", None),
    ("widgets", None),
    ("items", None),
    ("top", "top"),
    ("left", "left"),
    ("center", None),
    ("right", "right"),
    ("bottom", "bottom"),
    ("leftWidgets", "left"),
    ("rightWidgets", "right"),
    ("sidebarWidgets", "sidebar"),
    ("rightHeaderPlaceholder", "header-right"),
    ("footerWidgets", "footer"),
)

# A node is transparent only if it has nothing of its own to say. These are the
# keys that make it say something: styling that reaches the stylesheet, a rule
# that decides whether it is there at all, or a click handler. A div with a
# class is part of the page's design; a div without one is a box around a box.
_SPEAKS = ("conditionalVisibilitySettings", "conditionalEditabilitySettings",
           "onClickAction", "screenReaderHidden")

# Already printed on their parent's own line. A caption lives under `content` on
# a dynamic text and under `template` on a client template, and `content` is
# also where a layout keeps its widgets - so without this, descending by key
# name alone prints every caption twice: once as text and once as the object it
# came out of.
_RENDERED_INLINE = {
    "Pages$ClientTemplate",
    "Pages$ClientTemplateParameter",
    "Texts$Text",
    "Microflows$TextTemplate",
    "Pages$Appearance",
    "Pages$FormattingInfo",
    "Pages$GridSortBar",
    "Pages$GridSortItem",
    "Pages$WidgetValidation",
    "Pages$ConfirmationInfo",
}

# The type name is the structural vocabulary of the output, so it is worth not
# spelling it the way the file format does: 'LayoutGridRow' is four syllables
# for 'row', repeated on every row of every page.
_LABELS = {
    "Pages$DivContainer": "div",
    "Pages$LayoutGrid": "grid",
    "Pages$LayoutGridRow": "row",
    "Pages$LayoutGridColumn": "col",
    "Pages$ScrollContainerRegion": "region",
    "Pages$TableRow": "row",
    "Pages$TableColumn": "col",
    "Pages$GridControlBar": "buttons",
    "Pages$SearchBar": "search",
    "Pages$ListViewTemplate": "template",
    "Pages$WebLayoutContent": "content",
    "Pages$NativeLayoutContent": "content",
}


def _label(kind: str) -> str:
    return _LABELS.get(kind) or _short(kind).lower()


class _Walk:
    """Print the widget tree, keeping only what the page is rather than how it looks."""

    def __init__(self, reader: _Reader, referenced: set, default_buttons: set = ()):
        self.r = reader
        self.referenced = referenced
        self.default_buttons = default_buttons
        self.lines = []
        self.unknown = set()
        self.collapsed = 0

    # -- lines --

    def _emit(self, depth: int, text: str):
        self.lines.append(_INDENT * depth + text)

    # -- nodes --

    def node(self, node, depth: int, r: _Reader, siblings: int = 1):
        if not isinstance(node, dict):
            return
        kind = node.get("$Type")
        if not kind:
            return

        renderer = _WIDGETS.get(kind)
        if renderer:
            line = renderer(node, r)
        else:
            line = _w_generic(node, r, self.unknown)

        # A structural node with nothing of its own prints nothing, and its
        # children take its place. The one exception is a column among several:
        # dropping it would merge two columns into one list of widgets and say
        # the page looks like something it does not.
        if line is None:
            speaks = _appearance(node) or any(node.get(key) for key in _SPEAKS)
            if speaks or siblings > 1:
                # Among siblings, even an empty box has to stay: dropping one of
                # three columns would merge them into a single list of widgets
                # and describe a page that does not exist.
                line = _label(kind)
            else:
                self.collapsed += 1
                self.children(node, depth, r)
                return

        self._emit(depth, line + self.suffixes(node, r))
        self.children(node, depth + 1, r.within(_source_entity(node, r)))

    def suffixes(self, node: dict, r: _Reader) -> str:
        """The cross-cutting facts, in a fixed order so lines stay comparable."""
        parts = []

        visible = _condition(node.get("conditionalVisibilitySettings"), r)
        if visible:
            parts.append(f"?vis {visible}")

        editable = _condition(node.get("conditionalEditabilitySettings"), r)
        if editable:
            parts.append(f"?edit {editable}")

        click = _action(node.get("onClickAction"), r)
        if click:
            parts.append("-> " + click)

        style = _appearance(node)
        if style:
            parts.append(style)

        # A widget's name is dead weight - divContainer12 - until something else
        # on the page points at it: a microflow argument taking a grid's
        # selection, a data view listening to a list. Then it is the only way to
        # tell which widget was meant, so it is printed exactly then.
        name = node.get("name")
        if name and name in self.referenced:
            parts.append("#" + name)

        if node.get("$ID") in self.default_buttons:
            parts.append("(default)")

        return ("   " + "  ".join(parts)) if parts else ""

    def children(self, node: dict, depth: int, r: _Reader):
        for key, label in _CHILD_KEYS:
            value = node.get(key)
            if not value:
                continue

            items = value if isinstance(value, list) else [value]
            items = [
                item for item in items
                if isinstance(item, dict) and item.get("$Type") not in _RENDERED_INLINE
            ]
            if not items:
                continue

            if label:
                self._emit(depth, label + ":")
                depth_of_items = depth + 1
            else:
                depth_of_items = depth

            for item in items:
                # Which layout placeholder a block fills only matters on a page
                # that fills more than one, and 626 of this app's 630 layout
                # arguments are the only one on their page. The name is also the
                # one thing the argument itself carries, so it is printed here,
                # where the sibling count is known, rather than by the renderer.
                if len(items) > 1 and item.get("$Type") == "Pages$LayoutCallArgument":
                    self._emit(depth_of_items, _last(item.get("parameter") or "?") + ":")
                    self.children(item, depth_of_items + 1, r)
                    continue
                self.node(item, depth_of_items, r, len(items))

        self.custom_widget(node, depth, r)

    def custom_widget(self, node: dict, depth: int, r: _Reader):
        """A custom widget's configured properties, and any widgets dropped into it."""
        if node.get("$Type") != "CustomWidgets$CustomWidget":
            return

        schema = {}
        _property_types(node.get("type"), schema)
        self.properties(node.get("object"), schema, depth, r)

    def properties(self, obj, schema: dict, depth: int, r: _Reader):
        if not isinstance(obj, dict):
            return
        for key, text, widgets, objects in _widget_properties(obj, schema, r, self.unknown):
            if text:
                self._emit(depth, f"{key} = {text}")
            if widgets:
                self._emit(depth, f"{key}:")
                for widget in widgets:
                    self.node(widget, depth + 1, r, len(widgets))
            for i, nested in enumerate(objects):
                self._emit(depth, f"{key}[{i}]:")
                self.properties(nested, schema, depth + 1, r)


def _referenced_names(unit: dict) -> set:
    """Every widget name something else on the page points at.

    A widget's name - `divContainer12` - is dead weight until something refers to
    it, and then it is the only thing identifying which widget was meant. Three
    things refer to one, and the third is the reason this is a two-pass scan
    rather than a field lookup:

      * a PageVariable can stand for a widget's current selection instead of a
        parameter, naming the widget under `widget`;
      * a data view can listen to another widget, and a control bar names its
        default button;
      * an expression anywhere on the page can read `$listView3/Street`, where the
        variable is a widget's name spelled inside a string. Nothing marks those
        as references, so they are found by intersecting the `$tokens` of every
        expression with the set of names the page actually defines - which is
        why the names have to be collected before they can be matched.
    """
    defined, pointed_at, tokens = set(), set(), set()

    def walk(node):
        if isinstance(node, dict):
            kind = node.get("$Type") or ""
            if node.get("name") and kind.split("$")[0] in ("Pages", "CustomWidgets"):
                defined.add(node["name"])
            for key in ("widget", "listenTarget"):
                if node.get(key):
                    pointed_at.add(node[key])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and "$" in node:
            tokens.update(_VARIABLE_TOKEN.findall(node))

    walk(unit)
    return pointed_at | (defined & tokens)


def _default_buttons(unit: dict) -> set:
    """The $IDs a control bar names as the button its grid triggers on double-click.

    The only place this tool reads a $ID and means it: the reference is by $ID
    rather than by name, so the fact cannot be attached to the button any other
    way, and 'which button does double-clicking a row press' is not visible
    anywhere else on the page.
    """
    found = set()

    def walk(node):
        if isinstance(node, dict):
            if node.get("defaultButton"):
                found.add(node["defaultButton"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(unit)
    return found


# --- header ----------------------------------------------------------------

def _header(unit: dict, r: _Reader, default_lang: str, codes: list) -> list:
    kind = _short(unit.get("$Type")).lower()
    name = unit.get("$QualifiedName") or unit.get("name") or "?"

    # A layout keeps everything a page keeps at its top level one step down,
    # inside `content` - including the layout it is itself built on, which is
    # layout inheritance and the first thing you need when a page's chrome comes
    # from somewhere you cannot see.
    content = unit.get("content") if isinstance(unit.get("content"), dict) else {}
    if content.get("layoutType"):
        kind += " " + str(content["layoutType"]).lower()

    lines = [f"{name}   [{kind}]"]

    parameters = unit.get("parameters") or []
    if parameters:
        lines.append("params  " + " | ".join(
            f"{p.get('name', '?')}: {(p.get('parameterType') or {}).get('entity') or '?'}"
            for p in parameters
        ))

    variables = unit.get("variables") or []
    if variables:
        lines.append("vars    " + " | ".join(
            f"{v.get('name', '?')} = {_flat(v.get('defaultValue')) or 'empty'}"
            for v in variables
        ))

    call = unit.get("layoutCall") or content.get("layoutCall") or {}
    layout = call.get("layout")
    if layout:
        slots = [
            _last(a.get("parameter") or "")
            for a in call.get("arguments") or []
        ]
        suffix = f" ({', '.join(s for s in slots if s)})" if len(slots) > 1 else ""
        lines.append(f"layout  {layout}{suffix}")

    title = r.text(unit.get("title"))
    if title:
        lines.append(f'title   "{title}"')

    if unit.get("url"):
        lines.append(f"url     {unit['url']}")

    roles = unit.get("allowedRoles") or []
    if roles:
        lines.append("roles   " + ", ".join(roles))

    settings = []
    if unit.get("excluded"):
        settings.append("EXCLUDED FROM BUILD")
    if unit.get("exportLevel") not in (None, "", "Hidden"):
        settings.append(f"exportLevel={unit['exportLevel']}")
    if unit.get("popupWidth") or unit.get("popupHeight"):
        settings.append(f"popup={unit.get('popupWidth', 0)}x{unit.get('popupHeight', 0)}")
    if unit.get("templateCategory"):
        settings.append(f"category={unit['templateCategory']}")
    if settings:
        lines.append("access  " + " | ".join(settings))

    documentation = (unit.get("documentation") or "").strip()
    if documentation:
        lines.append(f"doc     {_flat(documentation, 1200)}")

    others = [code for code in codes if code != r.lang]
    note = f"lang    {r.lang}"
    if r.lang != default_lang and default_lang:
        note += f" (app default is {default_lang})"
    if others:
        note += f"; also {', '.join(others)}"
    lines.append(note)

    return lines


# --- tool ------------------------------------------------------------------

def read_page(name: str, lang: str = None, project: str = None) -> dict:
    """Return one page, snippet, layout, page template or building block as text."""
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

    found = [u for u in units if name in unit_names(u)]
    if not found:
        return {"success": False, "error": f"No unit found with the exact name: '{name}'"}

    # A qualified name is not unique across unit types: a page template takes the
    # name of the page it was made from. Narrowing by type resolves that without
    # bothering the caller - a simple name matching two modules is a real
    # ambiguity and still does.
    pages = [u for u in found if u.get("$Type") in PAGE_TYPES]

    if not pages:
        kinds = ", ".join(sorted({u.get("$Type", "?") for u in found}))
        return {
            "success": False,
            "error": (
                f"'{name}' is a {kinds}, which has no widget tree to linearize. "
                "Use read_microflow for a flow, or find_unit_by_name for anything else."
            ),
        }

    if len(pages) > 1:
        # A page template keeps the name of the page it was generated from, and
        # both are in this family, so narrowing by family is not enough: the
        # collision is between types, not between modules. PAGE_TYPES is in
        # preference order and a page is what someone asking for a name means,
        # so the first type that matches at all wins, and the error is reserved
        # for the ambiguity that preference cannot settle - the same name in two
        # modules.
        for kind in PAGE_TYPES:
            narrowed = [u for u in pages if u.get("$Type") == kind]
            if narrowed:
                pages = narrowed
                break

    if len(pages) > 1:
        described = sorted(
            f"{u.get('$QualifiedName') or u.get('name')} ({_short(u.get('$Type'))})"
            for u in pages
        )
        return {
            "success": False,
            "error": (
                f"'{name}' matches {len(pages)} documents: {', '.join(described)}. "
                "Use the qualified name."
            ),
        }

    unit = pages[0]
    reader = _Reader(chosen)
    walk = _Walk(reader, _referenced_names(unit), _default_buttons(unit))
    walk.children(unit, 0, reader)

    lines = _header(unit, reader, default_lang, codes) + [""] + walk.lines

    notes = []
    if walk.collapsed:
        notes.append(
            f"{walk.collapsed} container(s) that carried no class, condition or action "
            "were dropped and their contents printed in their place."
        )
    if walk.unknown:
        notes.append(
            "Printed generically, no dedicated renderer: "
            + ", ".join(sorted(walk.unknown))
        )
    if unit.get("imageData"):
        notes.append(
            f"A {len(unit['imageData']) // 1024} KB base64 preview image was not printed."
        )
    if notes:
        notes.append(f"For the exact model, use find_unit_by_name('{name}').")

    result = {
        "success": True,
        "name": unit.get("$QualifiedName") or unit.get("name"),
        "type": unit.get("$Type"),
        "language": chosen,
        "text": "\n".join(lines),
    }
    if notes:
        result["notes"] = notes
    return result


TOOL_DEFINITION = {
    "name": "read_page",
    "description": (
        "Read one page, snippet, layout, page template or building block as a "
        "linearized widget tree: the data each container reads, every caption and "
        "validation message, what each button calls with which arguments, and the "
        "conditions that decide what is visible. Use this to answer what a screen "
        "shows and does. For the exact model - $IDs, geometry, a diff - use "
        "find_unit_by_name on the same name."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": (
                    "Qualified name of a page, snippet, layout, page template or "
                    "building block - e.g. 'MyModule.Order_Edit' or "
                    "'MyModule.SNIP_OrderTotals'."
                ),
            },
            "lang": {
                "type": "string",
                "description": (
                    "Language code for the captions, e.g. 'en_US'. Defaults to the "
                    "app's own default language."
                ),
            },
        },
        "required": ["name"],
    },
}


if __name__ == "__main__":
    print(read_page("MyModule.Order_Edit")["text"])
