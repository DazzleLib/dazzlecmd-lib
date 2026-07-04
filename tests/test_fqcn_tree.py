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
        for key in ("tst", "tst:.meta", "tst:.meta:verb",
                    "tst:.kit", "tst:.level"):
            assert key in tree, key

    def test_real_axes_derived_not_listed(self, tree):
        # nodes exist because the OBJECTS exist (walked, not hand-listed)
        assert "tst:.meta:verb:activation" in tree
        assert "tst:.meta:verb:loading" in tree
        assert "tst:.kit:visibility" in tree

    def test_nested_subspace_descends(self, tree):
        assert "tst:.kit:visibility:visibility" in tree

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
