"""The Continuum of argument contracts a verb subscribes to (dazzlecmd#104).

Bare dispatch has one inviolable rule: ``dz <level-object> <level-params>``
passes every parameter straight through to the target. Verbs that take a
level-object as a TARGET (``setup`` today; any rung of the ladder
``{...fibers, libs, internaltools, tools, kits, aggregators, envs,
shells, ...supra}`` tomorrow) need a declared answer to "whose namespace
is the command line?" -- and the answer is not one winner but a
**continuum of contracts**, each a named variant a verb subscribes to:

    1. BARE:          dz <level-object> <level-params>
                      (the target owns everything; not a verb form)
    2. VERB_MEDIATED: dz <verb> <level-object> <verb-params> -- <level-params>
                      (the verb owns the space before ``--``; the target
                      owns everything after -- the documented v0.7.46 form)
    3. TARGET_FIRST:  dz <verb> <level-object> <level-params> -- <verb-params>
                      (the mirror ordering; reserved, no subscriber yet)

Modeling the subscription explicitly means NO SURPRISES: the variant a
verb speaks is a queryable fact, not folklore. ``VERB_CONTRACTS`` below
is the source of truth; the intended surfacing is a derived read-only
PROPERTY on the verb's node in the fiber plane -- ``dz
:.meta:verb:setup.contract`` answers ``verb-mediated`` through the
``:.`` ring, exactly like the config ring's derived reads -- plus the
prose surfaces (``dz <verb> -h``, ``dz info`` on verb nodes, doc
generators). The ladder-wide formalization and the property wiring are
tracked in dazzlecmd#104; this module is its seed.

``setup`` subscribes to VERB_MEDIATED: ``--yes`` / ``--dry-run`` are
generic verb concerns (preview-and-consent apply to EVERY setup target,
independent of any tool author's script policy), while a target's own
flags stay fully reachable after ``--``.

Future second axis (#104): the subscription becomes declarable by the
DISPATCHED OBJECT too -- a tool's ``.dazzlecmd.json`` could state
``"cli_contract"`` at attach time, ceding a ``--`` tail to the
aggregator for dispatch-level params (``dz <tool> <toolargs> --
<aggregator-args>``). Strictly opt-in: ``--`` is live POSIX convention
for many wrapped tools (grep, git), so undeclared ALWAYS means bare.

Surfacing rides the ring at the correct PLANE (#100 disjointness):
``.`` is the user's declarable data space (``.note``, ``.recipe:1``),
and ``contract`` is machinery -- so on a user-space object it reads
through the fiber ring, ``dz :core:mytool:.contract``, never as a
``.contract`` squatting in the user property namespace. The verb
spelling ``dz :.meta:verb:setup.contract`` stands because verb nodes
are already fiber-side (properties on ``:.``-plane nodes are
established usage, e.g. ``:.meta:config.list_view``).
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# Named contract variants (rungs of the contract continuum).
CONTRACT_BARE = "bare"
CONTRACT_VERB_MEDIATED = "verb-mediated"
CONTRACT_TARGET_FIRST = "target-first"

# The subscription registry: verb name -> contract variant.
# Verbs absent from this mapping have no level-param passthrough
# (their argparse surface owns the whole line, as before).
VERB_CONTRACTS: Dict[str, str] = {
    "setup": CONTRACT_VERB_MEDIATED,
}


def contract_for(verb: str) -> str:
    """The contract variant ``verb`` subscribes to (CONTRACT_BARE if none)."""
    return VERB_CONTRACTS.get(verb, CONTRACT_BARE)


def split_level_args(argv: List[str]) -> Tuple[List[str], List[str]]:
    """Split a verb-mediated command line at the FIRST ``--``.

    ``argv`` is the full argv with the verb at index 0 (engine dispatch
    shape). Returns ``(head, level_args)``: ``head`` is what argparse
    should see (verb, target, verb-params); ``level_args`` is everything
    after the first ``--``, owned verbatim by the level-object.

    Only applies when ``argv[0]`` subscribes to VERB_MEDIATED; otherwise
    returns ``(argv, [])`` unchanged. The split happens BEFORE argparse
    ever runs (the ``dz find`` in-house pattern) so the tail can never
    be rejected as "unrecognized arguments" -- the v0.7.46 gap this
    closes (#104).
    """
    if not argv or contract_for(argv[0]) != CONTRACT_VERB_MEDIATED:
        return list(argv), []
    if "--" not in argv:
        return list(argv), []
    split_at = argv.index("--")
    return list(argv[:split_at]), list(argv[split_at + 1:])


def join_for_shell(args: List[str]) -> str:
    """Quote-join forwarded level-args for a ``shell=True`` command string.

    ``setup.command`` blocks run through the platform shell, so appended
    level-args must be joined with host-correct quoting: ``list2cmdline``
    rules on Windows (cmd), POSIX shell quoting elsewhere. The
    ``setup.script`` path never needs this -- scripts get the args as an
    argv list, verbatim.
    """
    import os
    if not args:
        return ""
    if os.name == "nt":
        from subprocess import list2cmdline
        return list2cmdline(args)
    import shlex
    try:
        return shlex.join(args)  # 3.8+
    except AttributeError:  # pragma: no cover
        return " ".join(shlex.quote(a) for a in args)
