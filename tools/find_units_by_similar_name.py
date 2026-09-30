"""Fuzzy search over unit names."""

import re
from difflib import SequenceMatcher

from tools._utils import load_units, unit_names

# A query token is truncated to this many characters before being looked for
# inside a candidate name. Names carry the same stem under different
# inflections - Save / Saved / Saving, Register / Registration - so matching
# whole tokens misses the unit you asked for. Truncating the query instead of
# stemming keeps this language-agnostic, which matters because a model's names
# are written in whatever language its team speaks: it costs a few false
# positives that ranking pushes down, and it buys the inflected hits.
_STEM = 5

# Tokens shorter than this are dropped before matching. Short tokens carry
# almost no identifying signal and match everywhere: 'ACT', 'SUB', 'DS' and
# 'VAL' are conventional microflow prefixes shared by hundreds of units, and a
# three-letter fragment like 'new' hides inside 'Renewal'. Kept as a floor
# rather than a weight because these are noise, not weak evidence. If a query
# is made up entirely of short tokens, they are all used - a floor that
# filtered everything would answer nothing.
_MIN_TOKEN = 4

# difflib's ratio scores whole-string similarity, which penalises exactly what
# is normal in a Mendix name: reordered tokens ('SaveOrder' against
# 'Order_Save' scores 0.53) and length ('SaveOrder' against
# 'ACT_Order_SaveDraft' scores 0.43). So it is not the ranker here - it is the
# last tier, kept for the one thing it is good at: typos, where no token
# matches at all.
_TYPO_CUTOFF = 0.6

# Folders match easily - their names are short, and there are over a thousand of
# them in a large app - so they crowd out the microflow or page that was
# actually being looked for. They stay in the results but rank after everything
# else of the same quality, unless unit_type asks for them.
_FOLDER_TYPE = "Projects$Folder"

_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def _tokens(text: str) -> list[str]:
    """Split a name into the lowercased tokens worth matching on.

    Splits on separators and CamelCase humps, then drops tokens below
    `_MIN_TOKEN` - unless that would leave nothing, in which case the short
    ones are all there is to go on.
    """
    parts = [t.lower() for t in _SPLIT.split(text) if t]
    long_enough = [t for t in parts if len(t) >= _MIN_TOKEN]
    return long_enough or parts


def _score(query: str, query_tokens: list[str],
           candidate: str) -> tuple[int, float, float]:
    """Rank one candidate name against the query as (tier, weight, ratio).

    Lower tier is better; a higher weight is better; the ratio breaks the
    remaining ties. The ratio never decides whether something matches, except
    in the typo tier where nothing else can.
    """
    low = candidate.lower()
    ratio = SequenceMatcher(None, query.lower(), low).ratio()

    # Tier 0: the query appears verbatim. Someone who types 'Order_Save' gets
    # that unit first even if a shorter name is a closer string overall.
    if query.lower() in low:
        return 0, 1.0, ratio

    matched = [t for t in query_tokens if t[:_STEM] in low]

    if len(matched) == len(query_tokens):
        return 1, 1.0, ratio
    if matched:
        # Weight a partial match by how much of the query it accounts for, in
        # characters rather than in tokens. Counting tokens makes every token
        # worth the same, so two incidental matches outrank one substantial
        # one: a query like 'ZZZ_Order_WhichIsMissing' scored 'which' and
        # 'missing' inside unrelated names and pushed those above the unit
        # actually called Order_NewEdit.
        total = sum(len(t) for t in query_tokens)
        return 2, sum(len(t) for t in matched) / total, ratio
    if ratio >= _TYPO_CUTOFF:
        return 3, 0.0, ratio
    return 4, 0.0, ratio


def find_units_by_similar_name(name: str, limit: int = 10, unit_type: str = None,
                               project: str = None) -> dict:
    """Find the units whose names are closest to the one given.

    Use it when the exact name is unknown or possibly misspelled. It answers
    "which unit did you mean?", so it returns identity fields only - id, type,
    name - never the matching units' full JSON.
    """
    try:
        units = load_units(project)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error": str(e)}

    if not name or not name.strip():
        return {"success": False, "error": "Provide the 'name' parameter."}

    query_tokens = _tokens(name)
    if not query_tokens:
        return {
            "success": False,
            "error": f"'{name}' has no letters or digits to search on.",
        }

    # Best score per unit, keyed by $ID. A unit offers both its qualified and
    # its simple name, and either may be the better match - but it must not
    # occupy two of the caller's slots, and one spelling must never shadow
    # another unit that happens to share it. An earlier version indexed
    # {name: unit}, which made 509 units unreachable by their simple name.
    best = {}
    for unit in units:
        utype = unit.get("$Type") or ""
        if unit_type and unit_type.lower() not in utype.lower():
            continue

        for candidate in unit_names(unit):
            tier, weight, ratio = _score(name, query_tokens, candidate)
            if tier == 4:
                continue
            uid = unit.get("$ID")
            current = best.get(uid)
            if current is None or (tier, -weight, -ratio) < current[:3]:
                best[uid] = (tier, -weight, -ratio, unit)

    if not best:
        return {"success": False, "error": f"No unit found close to: '{name}'"}

    # Folder demotion is a rank penalty, not a filter: a folder still beats a
    # worse-tier microflow. It is dropped when unit_type was given, since asking
    # for a type is asking for that type.
    demote_folders = not unit_type

    def sort_key(item):
        tier, neg_weight, neg_ratio, unit = item[1]
        is_folder = demote_folders and unit.get("$Type") == _FOLDER_TYPE
        return (tier, neg_weight, is_folder, neg_ratio,
                unit.get("$QualifiedName") or unit.get("name") or "")

    ranked = sorted(best.items(), key=sort_key)

    results = [
        {
            "id": unit.get("$ID"),
            "type": unit.get("$Type"),
            "name": unit.get("$QualifiedName") or unit.get("name"),
            "score": round(-neg_ratio, 3),
        }
        for _, (tier, neg_weight, neg_ratio, unit) in ranked[:limit]
    ]

    out = {
        "success": True,
        "total_matched": len(ranked),
        "returned": len(results),
        "units": results,
    }
    if len(ranked) > len(results):
        out["note"] = (
            f"{len(ranked)} units matched and the closest {len(results)} are "
            "listed. Raise 'limit' to see more, or pass 'unit_type' to narrow "
            "by kind."
        )
    return out


TOOL_DEFINITION = {
    "name": "find_units_by_similar_name",
    "description": (
        "Fuzzy-search units of the Mendix app by name. Useful when the exact "
        "name is unknown, only partly remembered, or slightly wrong: it matches "
        "on word stems, so word order and inflection do not have to line up. "
        "Case does not matter, but word boundaries do: write 'save order' or "
        "'SaveOrder', not 'saveorder', since a single lowercase run cannot be "
        "split into words. "
        "Returns identity fields only - id, $Type and name - so use "
        "find_unit_by_name once you know which unit you meant. Folders rank "
        "last unless unit_type asks for them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Approximate unit name to look for",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of results (default: 10)",
                "default": 10,
            },
            "unit_type": {
                "type": "string",
                "description": (
                    "Restrict to a $Type, e.g. 'Microflows$Microflow' "
                    "(partial, case-insensitive). Optional."
                ),
            },
        },
        "required": ["name"],
    },
}


if __name__ == "__main__":
    import pprint
    pprint.pprint(find_units_by_similar_name("ACT_Sve"))
    pprint.pprint(find_units_by_similar_name("ACT_Sve", unit_type="Microflows$Microflow"))
