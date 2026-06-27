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

Slices 1-2 cover the **kit**, **aggregator**, and **tool** levels. The tool's
``state`` facet projects its mode (the read-side of ``dazzlecmd_lib.mode``), the
tool-level analogue of the kit's verb-axis ``axis_state``. Routing the dz-side
``dz info`` reads through this surface (with the deliberate byte-gate re-bless)
lands in the following SD-A slice.
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


def _tool_identity_fields(project, engine):
    """The tool's static identity field-set -- the identity rows of the old
    ``render_info`` (name/fqcn/kit/namespace/version/platform/...). The
    resolution banners (alias provenance, shadow status) stay in the dz
    resolution layer: this is the entity's identity, not how it was reached."""
    taxonomy = getattr(project, "taxonomy", None) or {}
    tags = taxonomy.get("tags") if isinstance(taxonomy, dict) else None
    category = taxonomy.get("category") if isinstance(taxonomy, dict) else None
    abs_fqcn = None
    if engine is not None and hasattr(engine, "absolute_fqcn"):
        a = engine.absolute_fqcn(project)
        if a and a != getattr(project, "fqcn", None):
            abs_fqcn = a
    return [
        ("Name", getattr(project, "name", None)),
        ("FQCN", getattr(project, "fqcn", None)),
        ("Absolute", abs_fqcn),
        ("Kind", "tool"),
        ("Kit", getattr(project, "kit_import_name", None)),
        ("Namespace", getattr(project, "namespace", None)),
        ("Version", getattr(project, "version", None)),
        ("Description", getattr(project, "description", None)),
        ("Platform", getattr(project, "platform", None) or "cross-platform"),
        ("Language", getattr(project, "language", None)),
        ("Category", category),
        ("Tags", ", ".join(tags) if tags else None),
    ]


def _tool_state_fields(project, engine, project_root):
    """The tool's dynamic state -- its mode -- as a field-set: the read-side of
    the mode system (the tool-level analogue of the kit's ``axis_state``). Mode
    is not yet a registered ``VerbAxis``, so it projects as a labelled value
    rather than ``{warm, cold}`` rungs; when SD-2 registers the mode subspace
    this becomes an axes projection like the kit's. Mode is filesystem-derived,
    so without a ``project_root`` there is nothing to read."""
    from dazzlecmd_lib import mode as _mode
    root = project_root if project_root is not None else getattr(
        engine, "project_root", None)
    if root is None:
        return [("Mode", None)]
    tools_dir = getattr(engine, "tools_dir", "projects")
    _state, label = _mode.classify_tool_state(project, root, tools_dir=tools_dir)
    return [("Mode", label)]


# ---------------------------------------------------------------------------
# membership / structure facets -- the read behind list/tree
# ---------------------------------------------------------------------------


def _tool_name(t):
    return t if isinstance(t, str) else (getattr(t, "name", None) or str(t))


def membership_rows(entity, engine, level, *, projects=None, kits=None):
    """The entity's containment members as ``(kind, name, detail)`` rows -- the
    data behind ``list``.

    **Invariant-full referent** (referent DWP 2026-06-26): an aggregator's
    members are its WHOLE subtree -- every kit AND every tool; a kit's members
    are its tools; a tool is a leaf (no modelled components yet). The no-target
    overview reads the aggregator, so the set does NOT shrink with the
    foreground level -- the level only moves the camera (relative naming /
    centering), it never filters which entities appear.
    """
    if level == "aggregator":
        kit_list = kits if kits is not None else (getattr(engine, "kits", []) or [])
        proj_list = projects if projects is not None else (
            getattr(engine, "projects", []) or [])
        rows = []
        for k in kit_list:
            kname = getattr(k, "kit_name", None) or getattr(k, "name", None)
            n = len(getattr(k, "tools", None) or [])
            rows.append(("kit", kname, f"{n} tool(s)"))
        for p in proj_list:
            rows.append(("tool", _tool_name(p), ""))
        return rows
    if level == "kit":
        return [("tool", _tool_name(t), "")
                for t in (getattr(entity, "tools", None) or [])]
    return []  # tool leaf -- components not modelled yet


def structure_rows(entity, engine, level, *, projects=None, kits=None):
    """The entity's containment subtree as ``(kit_name, [tool_names])`` groups
    -- the data behind ``tree`` (= ``membership`` recursed over containment).
    Same invariant-full referent as ``membership_rows``."""
    if level == "aggregator":
        kit_list = kits if kits is not None else (getattr(engine, "kits", []) or [])
        return [
            (getattr(k, "kit_name", None) or getattr(k, "name", None),
             [_tool_name(t) for t in (getattr(k, "tools", None) or [])])
            for k in kit_list
        ]
    if level == "kit":
        kname = getattr(entity, "kit_name", None) or getattr(entity, "name", None)
        return [(kname,
                 [_tool_name(t) for t in (getattr(entity, "tools", None) or [])])]
    return []


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

    **Card vs. catalog -- why ``membership``/``structure`` are opt-in.** These
    two facets use the stricter ``facets is not None and ... in facets`` guard
    (not ``want()``), so they are *excluded* from the default ``info`` card and
    appear only on explicit request. That is deliberate, not an oversight: they
    are the card-shaped, invariant-full *overview* of an entity's containment
    (an aggregator's whole kit+tool subtree as aligned rows). The rich
    ``dz list`` / ``dz tree`` *catalog* reads -- multi-section, ``--show`` modes,
    collision/alias markers, depth -- are a different read SHAPE, not a card,
    and they keep their own renderers (``rendering.render_list`` /
    ``render_tree``). The read family is unified at the meta-command layer (both
    are read verbs in the registry); it is NOT unified by forcing the catalog
    through the fields/axes facet model. See the list/tree reconciliation DWP
    (2026-06-27, decision S3 -- coexist): the card overview and the rich catalog
    are siblings, the catalog being the richer of the two. Surfacing these
    facets on the default card is a future, deliberate ``info`` re-bless -- not
    wired here.
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
        if facets is not None and "membership" in facets:
            sections.append(Section(
                name="membership", kind="list",
                rows=membership_rows(entity, engine, "kit"),
                title="Members:"))
        if facets is not None and "structure" in facets:
            sections.append(Section(
                name="structure", kind="tree",
                rows=structure_rows(entity, engine, "kit"),
                title="Structure:"))
    elif level == "aggregator":
        if want("identity"):
            name = getattr(entity, "name", None) or "aggregator"
            sections.append(Section(
                name="identity", kind="fields",
                rows=_aggregator_identity_fields(entity, projects, kits, project_root),
                title=f"Aggregator '{name}' -- identity card:"))
        # The aggregator gains no state facet until a later SD-A slice widens
        # axis_state to the aggregator's own axes.
        if facets is not None and "membership" in facets:
            sections.append(Section(
                name="membership", kind="list",
                rows=membership_rows(entity, engine, "aggregator",
                                     projects=projects, kits=kits),
                title="Members:"))
        if facets is not None and "structure" in facets:
            sections.append(Section(
                name="structure", kind="tree",
                rows=structure_rows(entity, engine, "aggregator",
                                    projects=projects, kits=kits),
                title="Structure:"))
    elif level == "tool":
        if want("identity"):
            tool_name = getattr(entity, "name", None) or "tool"
            sections.append(Section(
                name="identity", kind="fields",
                rows=_tool_identity_fields(entity, engine),
                title=f"Tool '{tool_name}' -- identity card:"))
        if want("state"):
            sections.append(Section(
                name="state", kind="fields",
                rows=_tool_state_fields(entity, engine, project_root),
                title="Current state:"))
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
        def _key(label):
            return label.lower().replace(" ", "_").replace("-", "_")
        payload = {}
        for sec in interro.sections:
            if sec.name == "state":
                # The state facet always nests under "state" -- the axis->rung
                # map (kit) or the labelled field map (the tool's mode).
                if sec.kind == "axes":
                    payload["state"] = {axis: cur for axis, cur, _w, _c in sec.rows}
                else:
                    payload["state"] = {_key(l): v for l, v in sec.rows}
            elif sec.kind == "fields":
                for label, value in sec.rows:
                    payload[_key(label)] = value
            elif sec.kind == "list":
                payload["members"] = [
                    {"kind": k, "name": n, "detail": d} for k, n, d in sec.rows]
            elif sec.kind == "tree":
                payload["structure"] = {kit: tools for kit, tools in sec.rows}
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
        elif sec.kind == "list":
            print()
            print(sec.title)
            for kind, name, detail in sec.rows:
                suffix = f"  ({detail})" if detail else ""
                print(f"  [{kind}] {name}{suffix}")
        elif sec.kind == "tree":
            print()
            print(sec.title)
            for kit_name, tools in sec.rows:
                print(f"  {kit_name}")
                for t in tools:
                    print(f"    - {t}")
    return 0
