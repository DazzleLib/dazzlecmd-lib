"""VerbAxis -- the cross-level verb registry primitive (SD-0, B1 slice 1).

Encodes the SD-0 acceptance checks (AC0-1..7) as runnable tests:

  * the ``{on, off}`` model: ``on`` resolves to ``warm``, ``off`` to ``cold``;
  * three-forms-one-handler: ``on``/``off``, the special name, and the canonical
    ``(axis, pole)`` identity all collapse to one pole;
  * the lib-primitive projections (``groupable()``/``continuum()``) are faithful
    -- "same bones" on dazzle-lib's ``Groupable``/``Continuum``;
  * the registry reproduces today's kit lifecycle + projection verbs with NO
    behaviour change (AC0-6), scoped ``applies_at={kit}``.

Design: 2026-06-25 SD-0 + the master plan (Gate D, contract H1). This slice does
NOT touch the mode/materialization subspace, so the SD-2 correction (materialization
is a presence axis, not a fiber of mode) leaves it untouched -- ``coupling`` is
carried here as an opaque token the cascade layer (SD-9) interprets later.
"""
import pytest

from dazzle_lib.groupable import Groupable
from dazzlecmd_lib.continuum import Continuum
from dazzlecmd_lib.verb_axis import (
    VerbAxis,
    VERB_AXES,
    ON,
    OFF,
    WARM,
    COLD,
    TOOL,
    KIT,
    AGGREGATOR,
    KNOWN_LEVELS,
    COUPLING_ALIGNED,
    COUPLING_INDEPENDENT,
    axis_by_name,
    resolve_special,
    canonical_identity,
    meta_tag_for,
    VERB_SPACE,
    VERB_LEVEL_SPACE,
    LEVEL_CONTINUUM,
    verb_axis_names,
)


def _loading():
    """The canonical binary axis used across the collapse tests."""
    return VerbAxis("loading", "attach", "detach", frozenset({KIT}),
                    COUPLING_ALIGNED, gloss="loaded vs a pointer")


# --- construction & validation (AC0-1) -------------------------------------
class TestConstruction:
    def test_minimal_axis_constructs(self):
        va = VerbAxis("activation", "enable", "disable", frozenset({KIT}))
        assert va.axis == "activation"
        assert va.warm == "enable"
        assert va.cold == "disable"
        assert va.applies_at == frozenset({KIT})
        # binary by default, independent coupling by default
        assert va.rungs == ()
        assert va.coupling == COUPLING_INDEPENDENT

    def test_is_frozen(self):
        va = _loading()
        with pytest.raises(Exception):  # FrozenInstanceError (a dataclass error)
            va.axis = "other"  # type: ignore[misc]

    @pytest.mark.parametrize("axis,warm,cold,applies", [
        ("", "attach", "detach", frozenset({KIT})),       # empty axis
        ("loading", "", "detach", frozenset({KIT})),       # empty warm
        ("loading", "attach", "", frozenset({KIT})),       # empty cold
        ("loading", "attach", "detach", frozenset()),      # empty applies_at
    ])
    def test_invalid_construction_raises(self, axis, warm, cold, applies):
        with pytest.raises(ValueError):
            VerbAxis(axis, warm, cold, applies)


# --- the {on, off} model: three-forms-one-handler (AC0-2..4) ---------------
class TestOnOffCollapse:
    def test_warm_is_is_the_warm_special(self):
        assert _loading().warm_is == "attach"

    def test_on_resolves_to_warm_off_to_cold(self):
        va = _loading()
        assert va.pole_of(ON) == WARM
        assert va.pole_of(OFF) == COLD

    def test_special_names_resolve_to_the_same_poles(self):
        va = _loading()
        # the heart of the collapse: `on` and the warm special are one pole
        assert va.pole_of("attach") == va.pole_of(ON) == WARM
        assert va.pole_of("detach") == va.pole_of(OFF) == COLD

    def test_pole_of_foreign_token_raises(self):
        va = _loading()
        with pytest.raises(KeyError):
            va.pole_of("enable")      # a different axis's special
        with pytest.raises(KeyError):
            va.pole_of("sideways")    # nonsense

    def test_verb_for_inverts_pole_of(self):
        va = _loading()
        assert va.verb_for(WARM) == "attach"
        assert va.verb_for(COLD) == "detach"
        with pytest.raises(KeyError):
            va.verb_for("warmish")

    def test_canonical_identity_is_axis_pole(self):
        va = _loading()
        assert va.canonical(WARM) == "verb:loading:warm"
        assert va.canonical(COLD) == "verb:loading:cold"
        with pytest.raises(KeyError):
            va.canonical("middle")

    def test_all_three_forms_collapse_to_one_canonical(self):
        """`dz loading on` == `dz loading attach` == `dz attach` -> one identity."""
        va = _loading()
        from_on = va.canonical(va.pole_of(ON))
        from_special = va.canonical(va.pole_of("attach"))
        from_resolve = va.canonical(resolve_special("attach")[1])
        assert from_on == from_special == from_resolve == "verb:loading:warm"


# --- lib-primitive projections: "same bones" (AC0-5) -----------------------
class TestProjections:
    def test_groupable_is_cold_minus_warm_plus(self):
        g = _loading().groupable()
        assert isinstance(g, Groupable)
        assert g.minus == "detach"   # cold = minus (not-P)
        assert g.plus == "attach"    # warm = plus  (P)
        assert g.meaning == "loading"

    def test_groupable_invert_swaps_poles(self):
        g = _loading().groupable()
        inv = g.invert()
        assert inv.plus == "detach" and inv.minus == "attach"

    def test_binary_continuum_warm0_cold_neg1(self):
        c = _loading().continuum()
        assert isinstance(c, Continuum)
        assert c.name == "loading"
        assert c.ranks == {"attach": 0, "detach": -1}

    def test_graded_continuum_runs_warm_to_cold(self):
        va = VerbAxis(
            "visibility", "visible", "shadowed", frozenset({TOOL}),
            rungs=("visible", "silenced", "hidden", "shadowed"))
        c = va.continuum()
        # warm rung at 0, descending to the cold pole
        assert c.ranks == {"visible": 0, "silenced": -1,
                           "hidden": -2, "shadowed": -3}


# --- the registry reproduces today's kit verbs (AC0-6) ---------------------
class TestRegistry:
    def test_axes_present(self):
        names = {va.axis for va in VERB_AXES}
        assert names == {"activation", "loading", "membership", "projection"}

    def test_special_names_match_todays_kit_pairs(self):
        pairs = {va.axis: (va.warm, va.cold) for va in VERB_AXES}
        assert pairs == {
            "activation": ("enable", "disable"),
            "loading": ("attach", "detach"),
            "membership": ("add", "remove"),
            "projection": ("favorite", "unfavorite"),
        }

    def test_all_slice1_axes_are_kit_scoped(self):
        for va in VERB_AXES:
            assert va.applies_at == frozenset({KIT}), va.axis

    def test_couplings_match_design(self):
        coupling = {va.axis: va.coupling for va in VERB_AXES}
        assert coupling["activation"] == COUPLING_ALIGNED
        assert coupling["loading"] == COUPLING_ALIGNED
        assert coupling["membership"] == COUPLING_ALIGNED
        assert coupling["projection"] == COUPLING_INDEPENDENT

    def test_special_names_are_unique_across_axes(self):
        """resolve_special must be deterministic: no two axes share a pole name."""
        specials = []
        for va in VERB_AXES:
            specials.extend([va.warm, va.cold])
        assert len(specials) == len(set(specials)), specials

    def test_known_levels_constant(self):
        assert KNOWN_LEVELS == frozenset({TOOL, KIT, AGGREGATOR})


# --- the hoisted/flat lookup (AC0-7) ---------------------------------------
class TestResolveSpecial:
    @pytest.mark.parametrize("name,axis,pole", [
        ("attach", "loading", WARM),
        ("detach", "loading", COLD),
        ("enable", "activation", WARM),
        ("disable", "activation", COLD),
        ("add", "membership", WARM),
        ("remove", "membership", COLD),
        ("favorite", "projection", WARM),
        ("unfavorite", "projection", COLD),
    ])
    def test_resolves_hoisted_special(self, name, axis, pole):
        va, got_pole = resolve_special(name)
        assert va.axis == axis
        assert got_pole == pole

    def test_on_off_are_not_hoistable(self):
        # `on`/`off` need an axis context -- they are not bare verbs
        assert resolve_special(ON) is None
        assert resolve_special(OFF) is None

    def test_unknown_name_is_none(self):
        assert resolve_special("teleport") is None

    def test_axis_by_name(self):
        assert axis_by_name("loading").warm == "attach"
        assert axis_by_name("nonexistent") is None


class TestCanonicalIdentityAndMetaTag:
    """The canonical ``verb:<axis>:<pole>`` identity + the generated ``_meta``
    dispatch tag bridge (SD-0 build-step 3)."""

    def test_canonical_identity_is_axis_pole(self):
        assert canonical_identity("loading", WARM) == "verb:loading:warm"
        assert canonical_identity("activation", COLD) == "verb:activation:cold"

    def test_canonical_identity_unknown_axis_raises(self):
        with pytest.raises(KeyError):
            canonical_identity("teleport", WARM)

    def test_meta_tag_reproduces_todays_kit_tags(self):
        # The generated <level>_<special> tag == the running CLI's _meta tags.
        assert meta_tag_for("activation", WARM, KIT) == "kit_enable"
        assert meta_tag_for("activation", COLD, KIT) == "kit_disable"
        assert meta_tag_for("loading", WARM, KIT) == "kit_attach"
        assert meta_tag_for("loading", COLD, KIT) == "kit_detach"
        assert meta_tag_for("membership", WARM, KIT) == "kit_add"
        assert meta_tag_for("membership", COLD, KIT) == "kit_remove"

    def test_meta_tag_wrong_level_raises(self):
        # loading.applies_at == {kit}; a tool-level loading tag is an error (AC0-4).
        with pytest.raises(ValueError):
            meta_tag_for("loading", WARM, TOOL)
        with pytest.raises(ValueError):
            meta_tag_for("activation", WARM, AGGREGATOR)

    def test_meta_tag_unknown_axis_raises(self):
        with pytest.raises(KeyError):
            meta_tag_for("teleport", WARM, KIT)

    def test_meta_tag_agrees_with_verb_for_pole(self):
        # The tag's special == the axis's verb for that pole, at every kit axis.
        for va in VERB_AXES:
            if KIT not in va.applies_at:
                continue
            for pole in (WARM, COLD):
                assert meta_tag_for(va.axis, pole, KIT) == f"kit_{va.verb_for(pole)}"


class TestVerbLevelSpace:
    """The ``(VERB x LEVEL)`` ContinuumSpace (SD-0 build-step 4) -- a scale-safe
    PRODUCT mirroring ``KIT_PRESENCE_SPACE``. AC0-5: composes + normal_forms
    without error; cross-axis/cross-level "warmer/colder" nav is refused."""

    def test_verb_space_has_an_axis_per_verb(self):
        # Top-level axes = the 4 binary VerbAxes PLUS the `mode` subspace (mode is
        # not a flat VerbAxis -- it joins as a nested ContinuumSpace).
        assert set(VERB_SPACE.axes) == set(verb_axis_names()) | {"mode"}
        assert verb_axis_names() == (
            "activation", "loading", "membership", "projection")
        # leaves() flattens the mode subspace -> mode.materialization / mode.upstream.
        assert set(VERB_SPACE.leaves()) == (
            set(verb_axis_names()) | {"mode.materialization", "mode.upstream"})

    def test_verb_level_space_composes_and_normal_forms(self):
        nf = VERB_LEVEL_SPACE.normal_form()   # must not raise (AC0-5)
        assert nf is not None
        leaves = set(VERB_LEVEL_SPACE.leaves())
        assert "level" in leaves
        assert any(name.endswith("activation") for name in leaves)

    def test_space_is_a_product_not_aligned(self):
        # PRODUCT (presence=None): no cross-axis/cross-level total order.
        assert VERB_SPACE.is_aligned is False
        assert VERB_LEVEL_SPACE.is_aligned is False

    def test_cross_axis_navigation_is_refused(self):
        # Scale-safety: aligned-only ops raise on the product (no "is loading
        # warmer than membership?", no "is tool warmer than aggregator?").
        for op, args in [("presence_of", ("kit",)), ("spectrum", ("kit",)),
                         ("slice", ("kit",)), ("cascade_to_neutral", ())]:
            fn = getattr(VERB_LEVEL_SPACE, op, None)
            if fn is None:
                continue
            with pytest.raises(Exception):
                fn(*args)

    def test_level_continuum_is_the_containment_ladder(self):
        assert LEVEL_CONTINUUM.levels() == ("tool", "kit", "aggregator")
        # aggregator is the warm (neutral 0) capstone; tool the coldest.
        assert LEVEL_CONTINUUM.rank("aggregator") == 0
        assert LEVEL_CONTINUUM.rank("tool") < LEVEL_CONTINUUM.rank("kit") < 0
