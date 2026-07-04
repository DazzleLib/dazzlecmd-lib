"""SD-FQCN-2 slice 2a -- the walker decision, as regression tests.

DECISION (spike 2026-07-04, tests/one-offs/spike_tree_walk.py): KEEP
dazzle_lib.walk()/children()/fold() -- same-bones confirmed on the real
composed spaces; DazzleTreeLib not needed. Two riders the spike found:
(1) a Continuum's RUNGS are ranks, not child nodes -- rung NODES must be
SYNTHESIZED by the derive layer; (2) walk() has NO cycle guard (infinite
on a back-reference) -- the derive layer carries a visited-set; the tree
is a DAG by construction and these tests pin the assumption.
"""
from dazzle_lib.continuum import Continuum, walk, fold
from dazzlecmd_lib.verb_axis import VERB_SPACE, LEVEL_CONTINUUM
from dazzlecmd_lib.contexts import KIT_PRESENCE_SPACE


class TestWalkRealSpaces:
    def test_verb_space_walks_axes_and_subspaces(self):
        keys = {"/".join(k) for k, _ in walk(VERB_SPACE)}
        assert {"activation", "loading", "membership", "projection",
                "mode"} <= keys

    def test_nested_subspace_descends(self):
        keys = {"/".join(k) for k, _ in walk(KIT_PRESENCE_SPACE)}
        assert "visibility/visibility" in keys  # a space INSIDE a space

    def test_rungs_are_not_children(self):
        # THE 2d FINDING: LEVEL_CONTINUUM walks as ONE node -- its rungs
        # (fiber..supra) are ranks, not child nodes. Rung nodes get
        # SYNTHESIZED by the derive layer. If this ever changes in the
        # bedrock, the derive layer must be revisited.
        assert len(list(walk(LEVEL_CONTINUUM))) == 1

    def test_walk_is_deterministic(self):
        a = [k for k, _ in walk(VERB_SPACE)]
        assert a == [k for k, _ in walk(VERB_SPACE)]


class TestFiberDescent:
    def test_three_deep_fiber_chain(self):
        inner = Continuum("innermost", ranks={"lo": -1, "hi": 0})
        mid = Continuum("mid", ranks={"a": -1, "b": 0}, fibers={"a": inner})
        outer = Continuum("outer", ranks={"x": -1, "y": 0}, fibers={"x": mid})
        keys = ["/".join(k) for k, _ in walk(outer)]
        assert keys == ["", "x", "x/a"]  # fibers keyed by their RUNG


class TestFoldCascade:
    def test_fold_composes_down_the_tree(self):
        # the cascade prototype: min-verbosity accumulates over depth
        result = fold(outer := Continuum(
            "o", ranks={"x": 0},
            fibers={"x": Continuum("i", ranks={"y": 0})}),
            lambda n: 0, lambda n, kids: min([0] + list(kids)) - 1)
        assert result == -2  # two levels of combine
