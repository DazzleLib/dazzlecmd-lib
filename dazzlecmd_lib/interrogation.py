"""The one read surface -- entity interrogation (SD-A, the read collapse).

A read at any level is ``interrogate(entity, engine, *, level, facets=None)``
returning an ordered list of facet ``Section``s (identity, state, ...), printed
by ``render_interrogation(...)`` as an aligned card or ``--json``. There is one
source of truth for "what can be read off an entity"; reductions (``status``,
``mode info``, a single facet) are the ``facets=`` argument, not a second code
path.

The ``state`` facet is the **read-projection of the mutate axes**: it derives
from the ``VERB_AXES`` registry via ``axis_state`` -- the kit's current rung on
each lifecycle axis. Presence is what you get when you read the verb registry
instead of toggling it; that's why the two never duplicate.

Slice 1 (this file) covers the **kit** and **aggregator** levels, reproducing
the dz-side cards byte-for-byte. The **tool** level (identity + mode state) and
the lib-wide removal of the separate ``status`` verb land in the following SD-A
slices.
"""

from __future__ import annotations

import json as _json
import types as _types
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Facet sections -- the data an interrogation returns (display is separate)
# ---------------------------------------------------------------------------


@dataclass
class Section:
    """One facet of an interrogation.

    ``kind == "fields"`` -> ``rows`` is a list of ``(label, value)`` pairs (an
    identity card). ``kind == "axes"`` -> ``rows`` is a list of
    ``(axis, rung, warm, cold)`` tuples (the per-axis state block). ``title`` is
    the section header line.
    """

    name: str
    kind: str
    rows: list = field(default_factory=list)
    title: str | None = None


@dataclass
class Interrogation:
    """An entity's read: its level plus the ordered facet sections."""

    level: str
    sections: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# state facet -- the read-projection of the verb-axis registry
# ---------------------------------------------------------------------------


def axis_state(kit, engine, project_root=None):
    """The kit's current rung on each lifecycle axis -- the read-side of the
    verb registry. Iterates the ``VERB_AXES`` whose ``applies_at`` includes the
    kit level; axes with no per-kit rung (the favorite/projection binding) are
    skipped. ``kit`` may be a kit entity or a kit name. Returns
    ``(rows, always_active)`` where ``rows`` is a list of
    ``(axis, rung, warm, cold)``, or ``(None, False)`` if the kit is absent.

    Establishes "presence = the read-projection of the mutate axes": a new
    ``VerbAxis`` surfaces here for free, so every read that shows state and
    every ``status``-style reduction stay in lockstep with the registry.
    """
    from dazzlecmd_lib.contexts import KitMembershipContext
    from dazzlecmd_lib.verb_axis import VERB_AXES, KIT

    kit_name = kit if isinstance(kit, str) else (
        getattr(kit, "kit_name", None) or getattr(kit, "name", None))

    kit_list = getattr(engine, "kits", []) or []
    match = next(
        (k for k in kit_list
         if (getattr(k, "kit_name", None) or getattr(k, "name", None)) == kit_name),
        None)
    if match is None:
        return None, False
    always = bool(getattr(match, "always_active", False))
    membership = KitMembershipContext(
        project_root, kit_list, boundary_fqcn=getattr(engine, "command", "dz"))
    ref = _types.SimpleNamespace(
        name=kit_name, kit_name=kit_name, always_active=always)
    pointer = membership.pointer_of(ref) is not None
    cfg = engine._get_user_config() if hasattr(engine, "_get_user_config") else {}
    disabled = (not always) and (
        kit_name in set((cfg or {}).get("disabled_kits") or []))

    def _rung(axis):
        # The kit's current pole on `axis`, or None if the axis carries no kit rung.
        if axis == "activation":
            return "disabled" if disabled else "active"
        if axis == "loading":
            return "pointer (detached)" if pointer else "loaded (attached)"
        if axis == "membership":
            return "member"
        return None

    rows = []
    for va in VERB_AXES:
        if KIT not in va.applies_at:
            continue
        cur = _rung(va.axis)
        if cur is None:
            continue
        rows.append((va.axis, cur, va.warm, va.cold))
    return rows, always


# ---------------------------------------------------------------------------
# identity facet -- the per-level static field-sets
# ---------------------------------------------------------------------------


def _kit_identity_fields(kit):
    """The kit's static identity field-set (name/kind/version/source/...)."""
    virtual = bool(getattr(kit, "virtual", False))
    tools = getattr(kit, "tools", None) or []
    count_label = "alias(es)" if virtual else "tool(s)"
    version = getattr(kit, "version", None)
    if version in (None, "", "0.0.0"):       # the entity default -> "unset"
        version = None
    return [
        ("Name", getattr(kit, "kit_name", None) or getattr(kit, "name", None)),
        ("Kind", "virtual kit" if virtual else "kit"),
        ("Description", getattr(kit, "description", None)),
        ("Version", version),
        ("Tools", f"{len(tools)} {count_label}"),
        ("Import name", getattr(kit, "kit_import_name", None)),
        ("Directory", getattr(kit, "directory", None)),
        ("Source", getattr(kit, "kit_source", None)),
        ("Always-active", "yes" if getattr(kit, "always_active", False) else "no"),
    ]


def _aggregator_identity_fields(engine, projects, kits, project_root):
    """The aggregator's static identity field-set. ``engine`` IS the entity."""
    vi = getattr(engine, "version_info", None)
    version = vi[0] if (isinstance(vi, (tuple, list)) and vi) else None
    return [
        ("Name", getattr(engine, "name", None)),
        ("Command", getattr(engine, "command", None)),
        ("Kind", "aggregator"),
        ("Description", getattr(engine, "description", None)),
        ("Version", version),
        ("Tools", f"{len(projects or [])} tool(s)"),
        ("Kits", f"{len(kits or [])} kit(s)"),
        ("Root", project_root),
    ]


# ---------------------------------------------------------------------------
# interrogate -- build the facet sections for an entity at a level
# ---------------------------------------------------------------------------


def interrogate(entity, engine, *, level, facets=None, project_root=None,
                projects=None, kits=None):
    """Read ``entity`` at ``level`` into an ordered list of facet ``Section``s.

    ``facets=None`` -> every facet applicable at the level (the full ``info``
    view). ``facets={"state"}`` -> just that section (a reduction; e.g.
    ``status`` / ``mode info``). The caller is responsible for resolving the
    entity and handling "not found"; ``interrogate`` assumes it exists.
    """
    def want(f):
        return facets is None or f in facets

    sections = []

    if level == "kit":
        if want("identity"):
            kit_name = (getattr(entity, "kit_name", None)
                        or getattr(entity, "name", None))
            sections.append(Section(
                name="identity", kind="fields",
                rows=_kit_identity_fields(entity),
                title=f"Kit '{kit_name}' -- identity card:"))
        if want("state"):
            rows, _always = axis_state(entity, engine, project_root)
            sections.append(Section(
                name="state", kind="axes", rows=rows or [],
                title="Current state:"))
    elif level == "aggregator":
        if want("identity"):
            name = getattr(entity, "name", None) or "aggregator"
            sections.append(Section(
                name="identity", kind="fields",
                rows=_aggregator_identity_fields(entity, projects, kits, project_root),
                title=f"Aggregator '{name}' -- identity card:"))
        # The aggregator gains no state facet until a later SD-A slice widens
        # axis_state to the aggregator's own axes.
    else:
        raise ValueError(f"interrogate: unsupported level {level!r}")

    return Interrogation(level=level, sections=sections)


# ---------------------------------------------------------------------------
# render -- the display layer (one card walker for every level)
# ---------------------------------------------------------------------------


def _print_entity_card(title, fields):
    """Walk a per-level field-set -- a list of ``(label, value)`` -- and print an
    aligned identity card. Absent values render as ``(none)`` rather than being
    silently dropped, so the card's shape is the same for every entity at a
    level. One walker serves every level's table; a new level is a new
    field-set, not a new renderer."""
    print(title)
    print()
    width = max((len(label) for label, _ in fields), default=0)
    for label, value in fields:
        shown = value if (value is not None and value != "") else "(none)"
        print(f"  {label + ':':<{width + 1}} {shown}")
    return 0


def _print_axis_rows(rows):
    """Print the ``(axis, rung, warm, cold)`` rows as the aligned state block."""
    for axis, cur, warm, cold in rows:
        print(f"  {axis:<12} {cur:<20} ({warm} <-> {cold})")


def render_interrogation(interro, *, as_json=False):
    """Print an ``Interrogation`` -- the aligned card (identity + any state
    section), or a JSON object mirroring the same data. The full ``info`` view
    and every facet reduction render through this one function."""
    if as_json:
        payload = {}
        for sec in interro.sections:
            if sec.kind == "fields":
                for label, value in sec.rows:
                    key = label.lower().replace(" ", "_").replace("-", "_")
                    payload[key] = value
            elif sec.kind == "axes":
                payload["state"] = {axis: cur for axis, cur, _w, _c in sec.rows}
        print(_json.dumps(payload, indent=2))
        return 0

    for sec in interro.sections:
        if sec.kind == "fields":
            _print_entity_card(sec.title, sec.rows)
        elif sec.kind == "axes":
            if sec.rows:
                print()
                print(sec.title)
                _print_axis_rows(sec.rows)
    return 0
