"""SD-FQCN-2 slice 2c -- the derived tree, channels-by-existing, and the
cascade (AC-2-1, AC-2-2 + the lazy-import and guard riders)."""

from __future__ import annotations

import sys

import pytest

from dazzlecmd_lib.fqcn_tree import (
    ChannelInfo,
    build_tree,
    derive_channels,
    effective_channel_verbosity,
)
from dazzlecmd_lib.property_store import PropertyStore


@pytest.fixture()
def tree():
    return build_tree("tst")


@pytest.fixture()
def store(tmp_path):
    return PropertyStore(config_dir=str(tmp_path))


class TestBuildTree:
    def test_mounts_exist_self_rooted(self, tree):
        # the verb-addressing scheme's intrinsic pair + the flagship home
        # 2d: the kit machinery lives UNDER the rung node (canonical
        # dz:.level:kit); ":.kit" is an ALIAS, not a node.
        for key in ("tst", "tst:.meta", "tst:.meta:verb",
                    "tst:.level", "tst:.level:kit"):
            assert key in tree, key

    def test_real_axes_derived_not_listed(self, tree):
        # nodes exist because the OBJECTS exist (walked, not hand-listed)
        assert "tst:.meta:verb:activation" in tree
        assert "tst:.meta:verb:loading" in tree
        assert "tst:.level:kit:visibility" in tree

    def test_nested_subspace_descends(self, tree):
        assert "tst:.level:kit:visibility:visibility" in tree

    def test_node_attrs_carry_the_live_object(self, tree):
        from dazzle_lib.continuum import Continuum
        node = tree.nodes["tst:.meta:verb:activation"]
        assert isinstance(node["obj"], Continuum)
        assert node["kind"] == "Continuum"

    def test_one_canonical_parent(self, tree):
        # a TREE: every node except the root has exactly one parent
        for key in tree.nodes:
            preds = list(tree.predecessors(key))
            assert len(preds) == (0 if key == "tst" else 1), key


class TestDeriveChannels:
    def test_ac_2_1_channel_exists_because_node_exists(self, tree):
        channels = derive_channels(tree)
        # a channel NOT in log_lib's legacy 12, existing purely because
        # its continuum-node exists:
        assert "tst:.meta:verb:activation" in channels
        info = channels["tst:.meta:verb:activation"]
        assert isinstance(info, ChannelInfo)
        assert info.default_verbosity == 0 and info.opt_in is False

    def test_every_node_has_a_channel(self, tree):
        assert set(derive_channels(tree)) == set(tree.nodes)


class TestCascade:
    def test_ac_2_2_outer_squelch_no_per_channel_writes(self, tree, store):
        # ONE write on the OUTER node...
        store.set("tst:.meta:verb.channels.verbosity", -3)
        # ...squelches every inner channel with zero writes on them:
        assert effective_channel_verbosity(
            tree, store, "tst:.meta:verb:activation") == -3
        assert effective_channel_verbosity(
            tree, store, "tst:.meta:verb:mode") <= -3

    def test_inner_can_be_quieter_never_louder(self, tree, store):
        store.set("tst:.meta:verb.channels.verbosity", -1)
        store.set("tst:.meta:verb:activation.channels.verbosity", -4)
        assert effective_channel_verbosity(
            tree, store, "tst:.meta:verb:activation") == -4  # quieter wins
        store.set("tst:.meta:verb:loading.channels.verbosity", 3)
        assert effective_channel_verbosity(
            tree, store, "tst:.meta:verb:loading") == -1  # louder does NOT

    def test_default_is_zero(self, tree, store):
        assert effective_channel_verbosity(tree, store, "tst:.level") == 0


class TestSeamRiders:
    def test_networkx_imports_lazily(self):
        # the module must be importable WITHOUT networkx loading (the
        # cold-CLI rider); only build_tree pulls it in.
        import importlib
        import dazzlecmd_lib.fqcn_tree as mod
        saved = sys.modules.pop("networkx", None)
        try:
            importlib.reload(mod)
            assert "networkx" not in sys.modules
        finally:
            if saved is not None:
                sys.modules["networkx"] = saved
            importlib.reload(mod)

    def test_shared_subtree_guard(self):
        # the 2a rider: a shared/back-referencing structure must not
        # recurse forever -- the derive layer's visited-set absorbs it.
        from dazzle_lib.continuum import Continuum
        a = Continuum("a", ranks={"p": 0})
        b = Continuum("b", ranks={"q": 0}, fibers={"q": a})
        object.__setattr__(a, "fibers", {"p": b})  # a true cycle
        g = build_tree("tst", mounts={":.cyc": b})
        assert "tst:.cyc" in g  # terminated, tree built


class TestRelocatability:
    """The user's architecture probe (2026-07-04): 'one of the tests of
    how good our system is, is how easy it is to move a continuum like
    verb to some other area with minimal pain.' PINNED as a feature: a
    move is ONE mount-table entry -- the tree, channels, and cascade all
    derive identically at the new address. (The real-world costs live
    OUTSIDE the code: persisted store keys ride `dz meta prop migrate`,
    and 2d's alias edges make the OLD address keep resolving.)"""

    def test_moving_the_verb_space_is_one_mount_edit(self, store):
        from dazzlecmd_lib.verb_axis import VERB_SPACE
        # relocate: verb moves OUT of meta to a top-level fiber home
        g = build_tree("tst", mounts={":.verb": VERB_SPACE})
        assert "tst:.verb:activation" in g          # derived at the new home
        assert "tst:.meta:verb:activation" not in g  # gone from the old one
        # channels + cascade work at the new address untouched:
        assert "tst:.verb:activation" in derive_channels(g)
        store.set("tst:.verb.channels.verbosity", -2)
        assert effective_channel_verbosity(g, store, "tst:.verb:activation") == -2


class TestRungSynthesis:
    """2d: rung nodes from ranks (the 2a finding) -- and verb POLES fall
    out of the same rule on the verb axes."""

    def test_level_rungs_are_nodes(self, tree):
        for rung in ("fiber", "lib", "internaltool", "tool", "kit",
                     "aggregator", "supra"):
            key = f"tst:.level:{rung}"
            assert key in tree, key
            # rung-ness = the axis/rank attrs. `kind` stays "rung" for a
            # plain rung but becomes the OBJECT's type when machinery
            # grafts onto it (kit hosts KIT_PRESENCE_SPACE -- the
            # one-node doctrine: rung node == machinery host).
            assert tree.nodes[key]["axis"] == "tst:.level"
            if rung == "kit":
                assert tree.nodes[key]["kind"] == "ContinuumSpace"
            else:
                assert tree.nodes[key]["kind"] == "rung"

    def test_verb_poles_are_rung_nodes(self, tree):
        # the verb-addressing scheme's verb nodes, derived not listed
        assert "tst:.meta:verb:activation:enable" in tree
        assert "tst:.meta:verb:activation:disable" in tree
        assert "tst:.meta:verb:loading:attach" in tree

    def test_rung_carries_axis_and_rank(self, tree):
        node = tree.nodes["tst:.level:kit"]
        assert node["axis"] == "tst:.level"
        assert isinstance(node["rank"], int)

    def test_kit_machinery_grafts_under_the_rung(self, tree):
        # ONE kit concept: the rung node is also the machinery's parent
        assert "tst:.level:kit:visibility" in tree
        assert list(tree.predecessors("tst:.level:kit:visibility")) == [
            "tst:.level:kit"]


class TestAliasesAndTouch:
    """2d: prefix-aware alias resolution + touch-canonicalization (the
    lazy half of the schema/data/foreign-key doctrine)."""

    def test_flagship_spelling_resolves(self, tree):
        from dazzlecmd_lib.fqcn_tree import resolve_path
        assert resolve_path(tree, "tst:.kit.channels.verbosity") == \
            "tst:.level:kit.channels.verbosity"
        assert resolve_path(tree, "tst:.kit:visibility") == \
            "tst:.level:kit:visibility"

    def test_non_aliased_paths_unchanged(self, tree):
        from dazzlecmd_lib.fqcn_tree import resolve_path
        assert resolve_path(tree, "tst:.level:kit") == "tst:.level:kit"
        assert resolve_path(tree, "tst.note") == "tst.note"
        # no false prefix match ("...kitchen" is NOT ":.kit" + extension)
        assert resolve_path(tree, "tst:.kitchen.x") == "tst:.kitchen.x"

    def test_touch_canonicalize_moves_the_key(self, tree, store):
        from dazzlecmd_lib.fqcn_tree import touch_canonicalize
        # the 2d AC: set at the OLD aliased address...
        store.set("tst:.kit.channels.verbosity", -3)
        key, value = touch_canonicalize(
            tree, store, "tst:.kit.channels.verbosity")
        # ...one touch: canonical key holds it, the old key is GONE
        assert key == "tst:.level:kit.channels.verbosity"
        assert value == -3
        assert store.get("tst:.level:kit.channels.verbosity") == -3
        assert store.get("tst:.kit.channels.verbosity") is None

    def test_touch_canonical_wins_over_stale_old(self, tree, store):
        from dazzlecmd_lib.fqcn_tree import touch_canonicalize
        store.set("tst:.level:kit.channels.verbosity", 2)   # canonical
        store.set("tst:.kit.channels.verbosity", -4)        # stale old
        key, value = touch_canonicalize(
            tree, store, "tst:.kit.channels.verbosity")
        assert value == 2                                    # canonical wins
        assert store.get("tst:.kit.channels.verbosity") is None  # cleaned

    def test_cascade_through_the_grafted_machinery(self, tree, store):
        # quieting the RUNG node squelches the machinery beneath it
        store.set("tst:.level:kit.channels.verbosity", -2)
        assert effective_channel_verbosity(
            tree, store, "tst:.level:kit:visibility:visibility") == -2
