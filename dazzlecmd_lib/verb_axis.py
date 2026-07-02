"""VerbAxis -- the cross-level verb registry primitive (SD-0).

Generalizes the kit-scoped ``KitVerbPair`` (dazzlecmd ``kit_verbs.py``) into a
level-agnostic axis the whole CLI can be projected from. A ``VerbAxis`` is one
``{P, not-P}`` Groupable on a named axis, addressable three ways that all route
to ONE handler:

    dz <axis> on|off <target>      # the universal grouped poles  (on -> warm)
    dz <axis> <special> <target>   # the special name, grouped     (loading attach)
    dz <special> <target>          # the special name, hoisted/flat (attach)

So ``attach == loading-on``: the special name is the *ungrouped* form (specific,
hoistable), ``on``/``off`` the *grouped* form (uniform, needs the axis noun) --
``{grouping, ungrouping} = {P, not-P}`` applied to the verb names themselves. The
verb-level analog of the tool shortname<->FQCN duality.

Lives in dazzlecmd-lib, built on dazzle-lib's ``Groupable``/``Continuum`` (H8: no
CLI verb name leaks into the bedrock). Design: SD-0
(2026-06-25 ``...SD-0-verb-registry-data-model-and-on-off-axis-layer.md``) + the
master plan (Gate D, contract H1).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional, Tuple

from dazzle_lib.groupable import Groupable
from dazzlecmd_lib.continuum import Continuum, ContinuumSpace
from dazzlecmd_lib.mode_space import MODE_SPACE

# The universal grouped poles (H1): on -> warm, off -> cold.
ON = "on"
OFF = "off"

WARM = "warm"
COLD = "cold"

# Cascade coupling (consumed by SD-9; carried, not interpreted here).
COUPLING_ALIGNED = "aligned"
COUPLING_INDEPENDENT = "independent"

# The real entity levels (MVP frozen scope, H9). Extensible: SD-6 adds the inward
# fiber (lib/internal-tool/intra); SD-7/#79 add the upper levels.
TOOL, KIT, AGGREGATOR = "tool", "kit", "aggregator"
KNOWN_LEVELS: FrozenSet[str] = frozenset({TOOL, KIT, AGGREGATOR})


@dataclass(frozen=True)
class VerbAxis:
    """One binary (or graded) verb-axis -- a ``{warm, cold}`` Groupable on ``axis``.

    ``warm`` is the +/P special name (``enable``/``attach``/``add``), the verb the
    universal ``on`` maps to; ``cold`` is the -/not-P special (``disable``/...),
    mapped from ``off``. ``applies_at`` is the set of levels where the axis is
    meaningful. ``coupling`` (aligned|independent) is read by the cascade layer
    (SD-9). ``rungs`` empty => a binary axis ``{warm, cold}``; non-empty => a graded
    axis whose rungs run warm..cold (binary-by-default, graded-by-extension).
    """

    axis: str
    warm: str
    cold: str
    applies_at: FrozenSet[str]
    coupling: str = COUPLING_INDEPENDENT
    rungs: Tuple[str, ...] = ()
    gloss: str = ""

    def __post_init__(self) -> None:
        if not self.axis:
            raise ValueError("VerbAxis.axis is required")
        if not self.warm or not self.cold:
            raise ValueError(
                f"VerbAxis({self.axis!r}) needs both warm and cold poles")
        if not self.applies_at:
            raise ValueError(
                f"VerbAxis({self.axis!r}).applies_at must be non-empty")

    # -- the on/off model -------------------------------------------------
    @property
    def warm_is(self) -> str:
        """The special name the universal ``on`` resolves to (== ``warm``)."""
        return self.warm

    def pole_of(self, token: str) -> str:
        """Map a token (``on``/``off`` OR a special name) to ``"warm"``/``"cold"``.

        The three-forms-one-handler collapse at the data layer:
        ``pole_of("on") == pole_of(self.warm)`` and ``pole_of("off") ==
        pole_of(self.cold)``. Raises ``KeyError`` for a token off this axis.
        """
        if token == ON or token == self.warm:
            return WARM
        if token == OFF or token == self.cold:
            return COLD
        raise KeyError(f"{token!r} is not a pole of axis {self.axis!r}")

    def verb_for(self, pole: str) -> str:
        """The special name for a pole (``"warm"`` -> ``self.warm``)."""
        if pole == WARM:
            return self.warm
        if pole == COLD:
            return self.cold
        raise KeyError(f"{pole!r} is not a pole (expected 'warm' or 'cold')")

    def canonical(self, pole: str) -> str:
        """The canonical ``(axis, pole)`` dispatch identity, e.g. ``verb:loading:warm``.

        All three addressing forms for a given (axis, pole) collapse to this one
        identity -- the future canonical ``_meta`` tag the registry dispatches on.
        """
        if pole not in (WARM, COLD):
            raise KeyError(f"{pole!r} is not a pole")
        return f"verb:{self.axis}:{pole}"

    # -- the lib-primitive projections (AC0-5: same bones) ----------------
    def groupable(self) -> Groupable:
        """The ``{P, not-P}`` atom (cold=minus, warm=plus)."""
        return Groupable(minus=self.cold, plus=self.warm, meaning=self.axis)

    def continuum(self) -> Continuum:
        """The ordered axis as a ``Continuum`` (warm=0 neutral, colder = negative).

        Binary: ``{warm: 0, cold: -1}``. Graded: the declared ``rungs`` run
        warm(0)..cold(negative). Mirrors ``VISIBILITY_CONTINUUM`` /
        ``ACTIVATION_CONTINUUM`` (warm pole at rank 0).
        """
        if self.rungs:
            ranks = {name: -i for i, name in enumerate(self.rungs)}
        else:
            ranks = {self.warm: 0, self.cold: -1}
        return Continuum(name=self.axis, ranks=ranks)


# ---------------------------------------------------------------------------
# The registry. Slice 1 reproduces today's kit lifecycle + projection pairs
# (dazzlecmd ``kit_verbs.LIFECYCLE_PAIRS`` + ``FAVORITE_PAIR``) as level-agnostic
# ``VerbAxis`` entries scoped ``applies_at={kit}`` -- a faithful reproduction
# (AC0-6: no behaviour change). Widening ``applies_at`` to tool/aggregator, and
# folding the graded ``visibility`` axis in, are later B1 slices.
# ---------------------------------------------------------------------------
VERB_AXES: Tuple[VerbAxis, ...] = (
    VerbAxis("activation", "enable", "disable", frozenset({KIT}),
             COUPLING_ALIGNED, gloss="active vs loaded-but-inactive"),
    VerbAxis("loading", "attach", "detach", frozenset({KIT}),
             COUPLING_ALIGNED, gloss="loaded vs a pointer (listed, not loaded)"),
    VerbAxis("membership", "add", "remove", frozenset({KIT}),
             COUPLING_ALIGNED, gloss="registered vs deregistered + trashed"),
    VerbAxis("projection", "favorite", "unfavorite", frozenset({KIT}),
             COUPLING_INDEPENDENT, gloss="a saved shortcut name"),
)


def axis_by_name(axis: str) -> Optional[VerbAxis]:
    """The ``VerbAxis`` with this ``axis`` name, or ``None``."""
    for va in VERB_AXES:
        if va.axis == axis:
            return va
    return None


def resolve_special(name: str) -> Optional[Tuple[VerbAxis, str]]:
    """Resolve a hoisted special verb name to ``(VerbAxis, pole)``.

    E.g. ``resolve_special("attach") == (loading_axis, "warm")`` -- the hoisted
    ``dz attach`` form. ``None`` if no axis owns the name. The universal ``on`` /
    ``off`` are NOT resolvable here (they need an axis context).
    """
    for va in VERB_AXES:
        if name == va.warm:
            return (va, WARM)
        if name == va.cold:
            return (va, COLD)
    return None


# ---------------------------------------------------------------------------
# The canonical identity + the dispatch bridge (SD-0 build-step 3 / T-1).
#
# Every ``(axis, pole, level)`` has ONE level-agnostic canonical identity
# ``verb:<axis>:<pole>`` -- the tag the three addressing forms collapse to. The
# *legacy* per-level ``_meta`` tag (``kit_attach``, ...) the running CLI already
# dispatches is GENERATED from the registry (``<level>_<special>``), so the
# canonical identity and the dispatch tag can never drift. ``meta_tag_for`` is the
# (axis,pole,level)->dispatch-tag half of SD-0's ``handler_for``; ``MetaCommandRegistry``
# (in the CLI) is the tag->callable half. Keeping the bridge here (not the callables)
# holds the layer boundary -- the lib names verbs, the CLI owns handlers (H8/AC0-7).
# ---------------------------------------------------------------------------

def canonical_identity(axis: str, pole: str) -> str:
    """The level-agnostic canonical dispatch identity ``verb:<axis>:<pole>``.

    The one identity ``dz <axis> on|off``, ``dz <axis> <special>`` and the hoisted
    ``dz <special>`` all collapse to (SD-0 T-1). Raises ``KeyError`` for an unknown
    axis or a bad pole.
    """
    va = axis_by_name(axis)
    if va is None:
        raise KeyError(f"no verb-axis named {axis!r}")
    return va.canonical(pole)


def meta_tag_for(axis: str, pole: str, level: str) -> str:
    """The legacy ``_meta`` dispatch tag a canonical ``(axis, pole, level)`` maps to.

    The GENERATED synonym (``<level>_<special>`` -- e.g. ``kit_attach``), derived
    from the registry so it never drifts from the canonical identity. Raises
    ``KeyError`` for an unknown axis; raises ``ValueError`` if the axis does not
    apply at ``level`` (AC0-4 -- a clear error, never a silent wrong-level
    dispatch). The returned tag is what ``MetaCommandRegistry.dispatch`` routes.
    """
    va = axis_by_name(axis)
    if va is None:
        raise KeyError(f"no verb-axis named {axis!r}")
    if level not in va.applies_at:
        raise ValueError(
            f"axis {axis!r} does not apply at level {level!r} "
            f"(applies_at={sorted(va.applies_at)})")
    return f"{level}_{va.verb_for(pole)}"


# ---------------------------------------------------------------------------
# The (VERB x LEVEL) ContinuumSpace (SD-0 build-step 4). Mirrors KIT_PRESENCE_SPACE
# (dazzlecmd_lib.contexts): a PRODUCT (compose, presence=None) -> scale-safe, no
# cross-axis or cross-level "warmer/colder" navigation. Used for help-grouping
# (the axis names are the help headers) + structural validation, NOT for cascade
# (that is the opt-in SD-9 mechanism). PRODUCT, not aligned: activation and
# membership are not "warmer/colder" than each other, and tool/kit/aggregator are
# not cascade-ordered here either.
# ---------------------------------------------------------------------------

# The containment level ladder as an ordered Continuum (aggregator = the
# innermost-public capstone at neutral 0). Extended per SD-6 (the inward
# fiber: fiber < lib < internaltool below tool) and SD-7 (the upper
# levels: supra above the aggregator -- envs, shells, the containing
# world). The original MVP rungs {tool, kit, aggregator} keep their
# ranks, so every existing comparison and the tie-break (which only ever
# SEES tool/kit/aggregator candidates -- extended rungs are stored-but-
# inert there by design) are unchanged. `dz level <rung>` validates
# against these ranks AT CALL TIME, so this extension widens the CLI
# with no further change (v2 contract 3f").
LEVEL_CONTINUUM = Continuum(
    "level", ranks={
        "fiber": -5,          # the inward mechanism plane (the ':.' world)
        "lib": -4,            # bedrock libraries (dazzle-lib et al.)
        "internaltool": -3,   # internal/vendored tools
        "tool": -2,
        "kit": -1,
        "aggregator": 0,
        "supra": 1,           # the containing world (envs, shells; ':+')
    })

# Mode is meaningful wherever an entity can be embodied/tracked -- tool, kit, AND
# aggregator (the de-vendoring precedent extends it down the inward fiber too). The
# binary VERB_AXES above are applies_at={kit} today, pending the SD-B widening.
MODE_APPLIES_AT: FrozenSet[str] = frozenset({TOOL, KIT, AGGREGATOR})

# The VERB product: one continuum per binary VerbAxis, PLUS the ``mode`` SUBSPACE
# (materialization x upstream) composed in as a NESTED member. Mode is not a binary
# {warm, cold} axis (its materialization rungs aren't one verb pair -- embodied<->
# referenced is the dev/publish switch, referenced<->absent is materialize/de-), so
# it joins the one MUTATE space as a ContinuumSpace, not a flat VerbAxis (compose
# accepts a sub-space member, exactly as KIT_PRESENCE_SPACE nests visibility).
_VERB_MEMBERS = {ax.axis: ax.continuum() for ax in VERB_AXES}
_VERB_MEMBERS["mode"] = MODE_SPACE
VERB_SPACE = ContinuumSpace.compose("verb", _VERB_MEMBERS)

# (VERB x LEVEL): the verb product composed with the level continuum.
VERB_LEVEL_SPACE = ContinuumSpace.compose(
    "verb_level", {"verb": VERB_SPACE, "level": LEVEL_CONTINUUM})


def verb_axis_names() -> Tuple[str, ...]:
    """The verb-axis names in registry order -- the help-grouping headers."""
    return tuple(ax.axis for ax in VERB_AXES)
