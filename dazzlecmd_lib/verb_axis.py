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
from dazzlecmd_lib.continuum import Continuum

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
