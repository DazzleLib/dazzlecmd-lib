"""The derived FQCN tree -- SD-FQCN-2's substrate (slice 2c).

Builds the fiber-plane tree by WALKING the real continuum objects (the
same-bones walker decision, 2026-07-04): the tree is DERIVED from
`dazzle_lib`'s structures, never hand-listed. Every node gets a channel
by existing (the channel doctrine); the cascade is a fold up the
ancestor chain (an outer node's quietness squelches its whole subtree,
issue #89).

THE GRAPH-BACKEND CONTRACT (user decision, 2026-07-04): the backend is
NetworkX **behind this seam**, imported LAZILY (a cold CLI pays imports
on the hot path -- property gets/sets never reach this module). The nx
API subset consumed here IS the interface contract `dazzle-graph-lib`
will implement, making the eventual swap an import change:

    nx.DiGraph() / G.add_node(key, **attrs) / G.add_edge(u, v)
    G.nodes[key] (attr mapping) / key in G / G.predecessors(key)

MOUNTS (the verb-addressing scheme, SD-FQCN-2 DWP 2026-07-04): the
intrinsic pair is `<root>:.level` (the containment axis) and
`<root>:.meta` (the internals); THE verb space lives at
`<root>:.meta:verb`; the kit machinery (the flagship
`:.kit.channels.verbosity` home) at `<root>:.kit`.
"""

from __future__ import annotations

from typing import Any, Dict, NamedTuple, Optional


class ChannelInfo(NamedTuple):
    """A node's output layer (every node has one by existing)."""

    bang_path: str
    default_verbosity: int = 0
    opt_in: bool = False


def _default_mounts():
    """The derived tree's mount table: fiber-plane path -> the REAL
    continuum object that lives there. Extending the tree = adding a
    mount (or, at 2d, synthesizing rung/verb nodes under these)."""
    from dazzlecmd_lib.verb_axis import VERB_SPACE, LEVEL_CONTINUUM
    from dazzlecmd_lib.contexts import KIT_PRESENCE_SPACE
    return {
        ":.meta:verb": VERB_SPACE,
        ":.level": LEVEL_CONTINUUM,
        # 2d (the canonical-node decision, 2026-07-04): the kit MACHINERY
        # grafts UNDER the kit RUNG node -- one kit concept, one node.
        # `:.kit` stays alive as an ALIAS (see ALIASES below): the
        # flagship spelling `<root>:.kit.channels.verbosity` keeps
        # resolving; touch-canonicalization re-homes stored keys lazily.
        ":.level:kit": KIT_PRESENCE_SPACE,
    }


# alias path-prefix -> canonical path-prefix (relative to the root).
# Prefix-aware: an alias rewrites ANY key extending it.
DEFAULT_ALIASES = {
    ":.kit": ":.level:kit",
}


def build_tree(root: str, mounts: Optional[Dict[str, Any]] = None,
               aliases: Optional[Dict[str, str]] = None):
    """Derive the fiber-plane tree for the aggregator named ``root``
    (SELF-rooted: pass ``engine.command``). Returns an ``nx.DiGraph``
    whose node keys are canonical bang-paths and whose node attrs carry
    ``obj`` (the live RungValue) and ``kind`` (the type name).

    The walk carries a visited-set (id-based): the structures are DAGs
    by construction, but `dazzle_lib.walk` itself has no cycle guard
    (the 2a spike) -- the guard lives here, in the derive layer.
    """
    import networkx as nx  # LAZY -- see the module docstring
    from dazzle_lib.continuum import children

    g = nx.DiGraph()
    g.add_node(root, obj=None, kind="aggregator-root")
    seen = set()

    def mount(base: str, obj: Any) -> None:
        # ensure intermediate namespace nodes exist (e.g. ':.meta')
        parts = base.split(":")
        prefix = root
        for part in parts[1:]:  # parts[0] == '' before the first ':'
            child = f"{prefix}:{part}"
            if child not in g:
                g.add_node(child, obj=None, kind="namespace")
                g.add_edge(prefix, child)
            prefix = child
        _graft(prefix, obj)

    def _graft(at: str, obj: Any) -> None:
        if id(obj) in seen:
            return  # the cycle/shared-subtree guard (2a rider)
        seen.add(id(obj))
        g.nodes[at]["obj"] = obj
        g.nodes[at]["kind"] = type(obj).__name__
        for name, child_obj in children(obj).items():
            child = f"{at}:{name}"
            if child not in g:
                g.add_node(child, obj=None, kind="pending")
                g.add_edge(at, child)
            _graft(child, child_obj)

    # 2d: synthesize RUNG NODES first (the 2a finding: rungs are ranks,
    # not children) so mounts may graft UNDER a rung (":.level:kit").
    # Order: mount the plain continua, synthesize their rungs, then
    # graft the remaining mounts (deepest bases last).
    table = dict(mounts or _default_mounts())
    for base in sorted(table, key=lambda b: b.count(":")):
        mount(base, table[base])
        _synthesize_rungs(g)
    if aliases is None:
        aliases = dict(DEFAULT_ALIASES) if mounts is None else {}
    g.graph["aliases"] = {
        f"{root}{a}": f"{root}{c}" for a, c in aliases.items()
    }
    return g


def _synthesize_rungs(g) -> None:
    """Every Continuum node gains a child node PER RUNG (kind="rung").
    On the level axis this yields the rung nodes (`:.level:kit`); on a
    verb axis the rungs ARE the verb poles (`:.meta:verb:activation:enable`)
    -- the verb-addressing scheme's verb nodes fall out of the same rule."""
    from dazzle_lib.continuum import Continuum
    for key in list(g.nodes):
        obj = g.nodes[key].get("obj")
        if isinstance(obj, Continuum):
            for rung in obj.levels():
                child = f"{key}:{rung}"
                if child not in g:
                    g.add_node(child, obj=None, kind="rung",
                               axis=key, rank=obj.rank(rung))
                    g.add_edge(key, child)


def resolve_path(tree, path: str) -> str:
    """Canonicalize ``path`` through the tree's alias table (prefix-aware:
    `<root>:.kit.channels.verbosity` -> `<root>:.level:kit.channels...`).
    Longest alias prefix wins; non-aliased paths return unchanged."""
    aliases = tree.graph.get("aliases", {})
    best = None
    for alias in aliases:
        if (path == alias or path.startswith(alias + ".")
                or path.startswith(alias + ":")):
            if best is None or len(alias) > len(best):
                best = alias
    if best is None:
        return path
    return aliases[best] + path[len(best):]


def touch_canonicalize(tree, store, path: str):
    """The lazy half of the schema/data/foreign-key doctrine: resolve
    ``path`` through the aliases; if the STORE holds a value under the
    OLD spelling, MOVE it to the canonical key (write-first, delete-second
    -- the crash-safe MOVE ordering; canonical wins on any re-read, the
    delete retries on the next touch). Returns (canonical_key, value)."""
    canonical = resolve_path(tree, path)
    value = store.get(canonical)
    if canonical != path:
        old = store.get(path)
        if old is not None:
            if value is None:
                store.set(canonical, old)
                value = old
            store.delete(path)
    return canonical, value


def derive_channels(tree) -> Dict[str, ChannelInfo]:
    """Every node's channel, BY EXISTING -- the derived replacement for a
    hand-maintained channel list. Keys are the node bang-paths; the
    legacy flat names (log_lib's 12) stay valid via the transition UNION
    at the OutputManager seam (2e)."""
    return {
        key: ChannelInfo(bang_path=key)
        for key in tree.nodes
    }


def effective_channel_verbosity(tree, store, node_path: str) -> int:
    """The cascade (#89): a node's EFFECTIVE verbosity is the MINIMUM of
    its own resolved verbosity and every ancestor's, so quieting an
    outer node squelches its whole subtree with no per-channel writes.
    Resolution per node: the stored property ``<node>.channels.verbosity``
    (the SD-FQCN-3 store) else the derived default (0)."""
    def resolved(key: str) -> int:
        value = store.get(f"{key}.channels.verbosity")
        return value if isinstance(value, int) else 0

    level = resolved(node_path)
    current = node_path
    while True:
        preds = list(tree.predecessors(current))
        if not preds:
            break
        current = preds[0]  # a tree: exactly one canonical parent
        level = min(level, resolved(current))
    return level
