"""THE PRESENTATION CONTINUUM (P-1; the presentation-continuum DWP
2026-07-18, D-P1/D-P2): per-target information volume as a first-class
axis -- READ = SCOPE x DEPTH. Commands are compositions: `info` =
self@card, `list` = children@row, `tree` = subtree@row; the NID dump
(Phase 5) renders the deepest rung. card sits at 0 -- the invariant
seat ("the standard answer about one thing")."""

from dazzle_lib.continuum import Continuum

PRESENTATION_CONTINUUM = Continuum(
    "presentation",
    ranks={"value": -2, "row": -1, "card": 0, "full": 1, "dump": 2},
)

RUNG_HELP = {
    "value": "the bare read -- one property's value, nothing else "
             "(dz :core:x.note)",
    "row": "one line per target -- name, short help, markers (a list row)",
    "card": "the standard answer about one thing -- today's `info` "
            "(the invariant seat)",
    "full": "the card plus every facet: ring, properties, state "
            "(info --detail territory)",
    "dump": "everything -- every {Unified, Groupable, Continuum, "
            "ContinuumSpace} path leading to or intersecting the node "
            "and how (the NID rung)",
}

AXIS_HELP = ("How much a READ returns, per target -- commands compose "
             "SCOPE (self/children/subtree) with a DEPTH rung: info = "
             "self@card, list = children@row, tree = subtree@row. "
             "Sibling axis: :.meta:verbosity (what the machinery says "
             "while working).")


def graft_presentation_help(engine, tree) -> None:
    """Self-describing rung cards (AC-1): help attaches post-build via
    the extension pattern (rung synthesis carries no help)."""
    axis = f"{engine.command}:.meta:presentation"
    if axis in tree:
        tree.nodes[axis].setdefault("help", AXIS_HELP)
        for rung, text in RUNG_HELP.items():
            key = f"{axis}:{rung}"
            if key in tree:
                tree.nodes[key].setdefault("help", text)
