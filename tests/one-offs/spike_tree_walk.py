"""SD-FQCN-2 slice 2a -- the walker spike.

QUESTION (the user's directive): is dazzle_lib's walk()/children()/fold()
robust enough for the FQCN tree's complicated cases, or do we need
DazzleTreeLib? GATING CRITERION: "same bones" -- the walker must speak
Continuum/Groupable/fibers natively.

Probes: (1) full walks over the REAL composed spaces (VERB_SPACE,
KIT_PRESENCE_SPACE, LEVEL_CONTINUUM); (2) key ordering determinism;
(3) fiber descent (a Continuum with fibers hung on rungs); (4) cycle
behavior when a fiber back-references an ancestor (the DAG assumption);
(5) fold as the cascade prototype (min-verbosity down a subtree).

Run: python tests/one-offs/spike_tree_walk.py
"""
import sys

from dazzle_lib.continuum import Continuum, children, walk, fold
from dazzlecmd_lib.verb_axis import VERB_SPACE, LEVEL_CONTINUUM
from dazzlecmd_lib.contexts import KIT_PRESENCE_SPACE


def probe(title, fn):
    print(f"\n=== {title} ===")
    try:
        fn()
    except RecursionError:
        print("!! RecursionError (cycle not handled)")
    except Exception as exc:
        print(f"!! {type(exc).__name__}: {exc}")


def p1_real_spaces():
    for name, space in [("VERB_SPACE", VERB_SPACE),
                        ("KIT_PRESENCE_SPACE", KIT_PRESENCE_SPACE),
                        ("LEVEL_CONTINUUM", LEVEL_CONTINUUM)]:
        nodes = list(walk(space))
        print(f"{name}: {len(nodes)} nodes")
        for key, node in nodes[:6]:
            print(f"   {'/'.join(key) or '(root)':45s} {type(node).__name__}")
        if len(nodes) > 6:
            print(f"   ... +{len(nodes)-6} more")


def p2_determinism():
    a = [k for k, _ in walk(VERB_SPACE)]
    b = [k for k, _ in walk(VERB_SPACE)]
    print("two walks identical:", a == b)


def p3_fiber_descent():
    inner = Continuum("innermost", ranks={"lo": -1, "hi": 0})
    mid = Continuum("mid", ranks={"a": -1, "b": 0},
                    fibers={"a": inner})
    outer = Continuum("outer", ranks={"x": -1, "y": 0},
                      fibers={"x": mid})
    keys = ["/".join(k) for k, _ in walk(outer)]
    print("3-deep fiber chain keys:", keys)
    print("reaches innermost:", any("innermost" in ".".join(k) or "a" in k
                                    for k in keys) or keys)


def p4_cycle():
    a = Continuum("a", ranks={"p": 0})
    b = Continuum("b", ranks={"q": 0}, fibers={"q": a})
    # back-reference: a's fiber points at b -> a true cycle
    object.__setattr__(a, "fibers", {"p": b})
    sys.setrecursionlimit(500)
    nodes = []
    for i, (key, node) in enumerate(walk(b)):
        nodes.append(key)
        if i > 50:
            print("!! walked >50 nodes on a 2-node cycle -- INFINITE (bounded by guard)")
            return
    print(f"cycle walked {len(nodes)} nodes (terminated?)")


def p5_fold_cascade():
    # cascade prototype: min verbosity down a subtree
    leafv = lambda node: 0
    def combine(node, child_vals):
        return min([leafv(node)] + list(child_vals)) - 1
    result = fold(LEVEL_CONTINUUM, leafv, combine) if fold.__code__.co_argcount >= 3 else None
    print("fold signature:", fold.__code__.co_varnames[:fold.__code__.co_argcount])
    print("fold over LEVEL_CONTINUUM:", result)


probe("P1: the real composed spaces", p1_real_spaces)
probe("P2: determinism", p2_determinism)
probe("P3: fiber descent (3 deep)", p3_fiber_descent)
probe("P4: cycle behavior (the DAG assumption)", p4_cycle)
probe("P5: fold as the cascade prototype", p5_fold_cascade)
print("\nspike complete")
