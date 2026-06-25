"""Cross-level target resolution: resolve a bare name to ``(entity, level)``.

The verb x level CLI homogenization (dazzlecmd 0.11.x / SD-1) lets a verb act
at any level it applies to: ``dz <verb> <target>`` where ``<target>`` may name a
tool, a kit, or an aggregator. ``AggregatorEngine.resolve_target`` is the
resolver; this module holds its small value types.

The collision policy (SD-1 "P-2", mutation-class split):

- An explicit ``--as <level>`` (or a name favorite) ALWAYS pins the level.
- A bare name that matches exactly one level resolves to it.
- A bare name that matches MORE than one level:
    * for a READ verb (info/status/...): auto-pick by precedence
      ``tool > kit > aggregator`` and carry a ``notification`` naming the
      other-level matches -- low stakes, discoverable.
    * for a MUTATING verb: raise :class:`AmbiguousLevelError` listing the
      candidates + the ``--as`` hint, and act on NOTHING -- a wrong-level
      mutation is impossible by construction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# The 3 real entity levels -- identical to ``entity.type`` (Tool/Kit/Aggregator).
# The inward fiber (lib/internal-tool) and upper levels (environment/...) are
# DWP-gated expansions, not part of this set yet.
LEVELS = ("tool", "kit", "aggregator")

# Read auto-pick order: the more specific entity wins a bare ambiguous read.
_READ_PRECEDENCE = {"tool": 0, "kit": 1, "aggregator": 2}


class AmbiguousLevelError(Exception):
    """A bare name matched >1 level for a MUTATING verb -- refuse to guess (P-2).

    Carries the candidate ``(level, entity)`` list so the caller can render the
    options, and the message already includes the ``--as`` escape hint.
    """

    def __init__(self, name, candidates, command="dz"):
        self.name = name
        self.candidates: List[Tuple[str, object]] = list(candidates)
        levels = [lvl for lvl, _ in self.candidates]
        super().__init__(
            f"{command}: '{name}' is ambiguous -- it names "
            f"{' and '.join('a ' + lvl for lvl in levels)}. "
            f"Re-run with --as <{'|'.join(levels)}> to choose one "
            f"(nothing was changed)."
        )


@dataclass
class TargetResolution:
    """The result of :meth:`AggregatorEngine.resolve_target`.

    ``entity`` resolved at ``level``. ``notification`` is set only on a read
    auto-pick (the disambiguation message to print to stderr). ``candidates``
    lists every ``(level, entity)`` match when the name was ambiguous (empty on
    a clean single hit). ``tool_context`` is the underlying ``FQCNIndex``
    ``ResolutionContext`` when ``level == "tool"`` (so existing tool-info
    provenance banners keep working), ``None`` otherwise.
    """

    entity: object
    level: str
    notification: Optional[str] = None
    candidates: List[Tuple[str, object]] = field(default_factory=list)
    tool_context: Optional[object] = None


__all__ = ["LEVELS", "AmbiguousLevelError", "TargetResolution"]
