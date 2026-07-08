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
    resolve_path,
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
            # rung-ness = the ROLE (2026-07-06: kind is the LADDER TYPE;
            # a plain rung is a degenerate Unified; machinery grafting
            # onto a rung upgrades the TYPE, never the role).
            assert tree.nodes[key]["axis"] == "tst:.level"
            assert tree.nodes[key]["role"] == "rung"
            if rung == "kit":
                assert tree.nodes[key]["kind"] == "ContinuumSpace"
            else:
                assert tree.nodes[key]["kind"] == "Unified"

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


class TestCanonicalizationInvariant:
    """THE CORRECTIVE (postmortem 2026-07-04): every derived-tree node's
    canonical path must canonicalize to ITSELF -- identity over the whole
    tree. This single invariant would have caught the ':.level:kit'
    forgiveness misfire the moment 2d synthesized rung nodes: example
    vectors pin decisions at creation-time; an invariant over the DERIVED
    structure re-validates every prior decision each time the vocabulary
    changes."""

    def test_every_tree_node_is_canonical_fixed_point(self, tree):
        from dazzlecmd_lib.fqcn_grammar import canonicalize, FQCNParseError
        misfires = []
        for key in tree.nodes:
            try:
                canon, forgiven = canonicalize(key)
            except FQCNParseError as exc:
                misfires.append((key, f"unparseable: {exc}"))
                continue
            if canon != key or forgiven:
                misfires.append((key, f"-> {canon} (forgiven={forgiven})"))
        assert not misfires, misfires

    def test_every_alias_resolves_into_the_tree(self, tree):
        from dazzlecmd_lib.fqcn_tree import resolve_path
        for alias, canonical in tree.graph["aliases"].items():
            assert canonical in tree, (alias, canonical)
            assert resolve_path(tree, alias) == canonical


class TestNumericAddressingGaps:
    """The cross-layer probe (user question 2026-07-05): does the numeric
    address system hold for ANONYMOUS additions end-to-end? Answer today:
    NO at two seams -- both pinned here so they must FLIP when the
    C2/B3 slice lands (these tests are the gap's tripwire)."""

    def test_fraction_anon_rung_resolves(self):
        # C2 LANDED (2026-07-08): the tripwire FLIPS -- rank segments
        # parse (grammar) and resolve (tree). The anon rung answers to
        # its self-naming rank spelling AND to rank lookup.
        from fractions import Fraction
        from dazzle_lib.continuum import Continuum
        from dazzlecmd_lib.fqcn_grammar import canonicalize
        v = Continuum("kit", ranks={"config": 2, "debug": 3})
        v = v.densify_between("config", "debug")  # anon "5/2"
        g = build_tree("tst", mounts={":.kit": v})
        assert "tst:.kit:5/2" in g
        canonical, _ = canonicalize("tst:.kit:5/2")   # grammar admits it
        assert canonical == "tst:.kit:5/2"
        assert resolve_path(g, "tst:.kit:5/2") == "tst:.kit:5/2"
        # rank lookup by INTEGER selects by rank attr, not name
        assert resolve_path(g, "tst:.kit:2") == "tst:.kit:config"
        assert resolve_path(g, "tst:.kit:3") == "tst:.kit:debug"

    def test_zero_law_selects_nucleus_else_self(self):
        # THE ZERO LAW (C2 DWP, Z-B): X:0 -> the materialized rank-0
        # seat when one exists, else X itself (degenerate nucleus).
        from dazzle_lib.continuum import Continuum
        v = Continuum("kit", ranks={"off": -1, "seat": 0, "on": 1})
        g = build_tree("tst", mounts={":.kit": v})
        assert resolve_path(g, "tst:.kit:0") == "tst:.kit:seat"
        v2 = Continuum("k2", ranks={"config": 2, "debug": 3})
        g2 = build_tree("tst", mounts={":.k2": v2})
        assert resolve_path(g2, "tst:.k2:0") == "tst:.k2"  # degenerate

    def test_integer_anon_rung_parses_under_vocabulary_mounts(self):
        # integers are legal names -- an int-anon rung under an
        # IN-VOCABULARY mount is already addressable (name-form).
        from dazzle_lib.continuum import Continuum
        from dazzlecmd_lib.fqcn_grammar import canonicalize
        m = Continuum("kit", ranks={"remove": -1, "add": 1})
        m = m.densify_between("remove", "add")  # anon "0"
        g = build_tree("tst", mounts={":.kit": m})
        assert "tst:.kit:0" in g
        canon, forgiven = canonicalize("tst:.kit:0")
        assert canon == "tst:.kit:0" and forgiven is False

    def test_custom_mounts_misfire_the_static_vocabulary(self):
        # The KNOWN interim limitation, demonstrated: a mount name
        # OUTSIDE FIBER_ROOTS forgives to the property plane -- the
        # tree-aware-canonicalization successor's tripwire (flip when
        # canonicalize consults the tree instead of the frozenset).
        from dazzle_lib.continuum import Continuum
        from dazzlecmd_lib.fqcn_grammar import canonicalize
        v = Continuum("verbosity", ranks={"a": 0, "b": 1})
        g = build_tree("tst", mounts={":.verbosity": v})
        assert "tst:.verbosity" in g          # a REAL node...
        canon, forgiven = canonicalize("tst:.verbosity")
        assert forgiven is True               # ...that today forgives AWAY
        assert canon == "tst.verbosity"


class TestKitFiberCompleteness:
    """The asymmetry find (user, 2026-07-05): EVERY kit-applicable
    lifecycle axis is kit-class machinery -- derived from applies_at."""

    def test_kit_rung_carries_all_applicable_axes(self, tree):
        kids = {n.rsplit(":", 1)[-1]
                for n in tree.successors("tst:.level:kit")}
        assert {"activation", "visibility", "loading",
                "membership", "projection"} <= kids

    def test_derived_not_duplicated(self, tree):
        # activation exists ONCE under kit (the presence space's copy;
        # the verb-axis skip rule)
        kids = [n for n in tree.successors("tst:.level:kit")
                if n.endswith(":activation")]
        assert len(kids) == 1


class TestTwoRepresentationConsistency:
    """The 2026-07-05 meta-find: the user's field catches were ALL
    disagreements between two representations of one fact -- so test
    AGREEMENT, not each side alone. These are the mechanized forms of
    three human finds; extend this class whenever a fact gains a second
    home."""

    def test_applies_at_agrees_with_the_tree(self, tree):
        # the kit-card asymmetry, generalized: every verb axis that
        # declares a level in applies_at is REACHABLE under that rung
        from dazzlecmd_lib.verb_axis import VERB_AXES
        for va in VERB_AXES:
            for level in va.applies_at:
                rung = f"tst:.level:{level}"
                assert rung in tree, (va.axis, level)
                kids = {n.rsplit(":", 1)[-1] for n in tree.successors(rung)}
                assert va.axis in kids, (
                    f"{va.axis} declares applies_at={level} but is not "
                    f"under {rung} -- registry and tree disagree")

    def test_shipped_axes_declare_their_zero(self, tree):
        # the 0-anchor confusion: an axis whose 0 is occupied must SAY
        # what is conserved there (invariant nonempty) -- the card can
        # then justify its own encoding
        from dazzle_lib.continuum import Continuum
        from dazzlecmd_lib.verb_axis import VERB_AXES
        verb_axis_names = {va.axis for va in VERB_AXES}
        for key in tree.nodes:
            obj = tree.nodes[key].get("obj")
            if not isinstance(obj, Continuum):
                continue
            if not any(r == 0 for r in obj.ranks.values()):
                continue
            if obj.name in verb_axis_names:
                # warm-at-0 verb axes: the B2 re-encode (symmetric +-1
                # with meaning-as-invariant) supplies these -- when B2
                # lands, DELETE this exemption so the invariant tightens
                continue
            assert obj.invariant, (
                f"{key} occupies rank 0 but declares no invariant -- "
                f"the card cannot justify its own zero")


class TestOntologyRule:
    """User find 2026-07-06: 'namespace' is not a kind in our system --
    every node carries a LADDER TYPE; role is a separate facet. An
    undeclared node is a Unified in degenerate form."""

    def test_no_node_without_a_ladder_type(self, tree):
        LADDER = {"Unified", "Groupable", "Continuum", "ContinuumSpace"}
        for key in tree.nodes:
            assert tree.nodes[key].get("kind") in LADDER, (
                key, tree.nodes[key].get("kind"))

    def test_meta_is_a_unified_with_namespace_role(self, tree):
        n = tree.nodes["tst:.meta"]
        assert n["kind"] == "Unified" and n["role"] == "namespace"
        assert n["obj"].label == "meta"
