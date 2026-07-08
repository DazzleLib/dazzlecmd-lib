"""Tests for ``dazzlecmd_lib.fqcn_grammar`` -- the bang-path parser.

The parse vectors are the SD-FQCN-1 build gate: every operator
(``:`` / ``.`` / ``:.`` / ``:+``), compound-first tokenizing, name rules,
round-trip, and the malformed-input rejections.
"""

from __future__ import annotations

import pytest

from dazzlecmd_lib.fqcn_grammar import (
    FQCNParseError,
    OP_FIBER,
    OP_PATH,
    OP_PROP,
    OP_SUPRA,
    ParsedPath,
    Segment,
    is_bangpath,
    parse,
    unparse,
)


# (input, expected root, expected [(op, name), ...]) -- the parse vectors.
PARSE_VECTORS = [
    ("dz", "dz", []),
    ("dazzlecmd", "dazzlecmd", []),
    ("dz:wtf", "dz", [(OP_PATH, "wtf")]),
    ("dz:wtf:kit", "dz", [(OP_PATH, "wtf"), (OP_PATH, "kit")]),
    ("dz:.kit", "dz", [(OP_FIBER, "kit")]),
    ("dz:+kit", "dz", [(OP_SUPRA, "kit")]),
    ("dz:grep.note", "dz", [(OP_PATH, "grep"), (OP_PROP, "note")]),
    (
        "dz:.kit.channels.verbosity",
        "dz",
        [(OP_FIBER, "kit"), (OP_PROP, "channels"), (OP_PROP, "verbosity")],
    ),
    ("dz:wtf:.kit", "dz", [(OP_PATH, "wtf"), (OP_FIBER, "kit")]),
    ("dazzlecmd:.config", "dazzlecmd", [(OP_FIBER, "config")]),
    # hyphen + underscore in names
    ("dz:grep.recipe-1", "dz", [(OP_PATH, "grep"), (OP_PROP, "recipe-1")]),
    ("dz:.kit.my_channel", "dz", [(OP_FIBER, "kit"), (OP_PROP, "my_channel")]),
    # the level-hint example path (all distinct operators in one path)
    (
        "dz:.verb:supra:management.channels.verbosity",
        "dz",
        [
            (OP_FIBER, "verb"),
            (OP_PATH, "supra"),
            (OP_PATH, "management"),
            (OP_PROP, "channels"),
            (OP_PROP, "verbosity"),
        ],
    ),
    # supra then deeper
    ("dz:+kit:tool", "dz", [(OP_SUPRA, "kit"), (OP_PATH, "tool")]),
]


@pytest.mark.parametrize("text,root,steps", PARSE_VECTORS)
def test_parse_vectors(text, root, steps):
    parsed = parse(text)
    assert parsed.root == root
    assert parsed.segments == tuple(Segment(op, name) for op, name in steps)


@pytest.mark.parametrize("text,_root,_steps", PARSE_VECTORS)
def test_round_trip(text, _root, _steps):
    # unparse(parse(x)) reproduces the canonical input byte-for-byte.
    assert unparse(parse(text)) == text


def test_compound_first_fiber_not_split():
    # ':.' must be ONE fiber op, never ':' + '.'
    parsed = parse("dz:.kit")
    assert parsed.segments[0].op == OP_FIBER
    assert OP_PATH not in {s.op for s in parsed.segments}


def test_compound_first_supra_not_split():
    parsed = parse("dz:+kit")
    assert parsed.segments[0].op == OP_SUPRA


MALFORMED = [
    "",                 # empty
    ":kit",             # operator-first (no root)
    "dz:",              # trailing operator
    "dz:.",             # trailing compound operator
    "dz::kit",          # double colon
    "dz:.KIT",          # uppercase name (names are lowercase)
    "dz..x",            # double property operator
    "dz:grep..note",    # double dot mid-path
    "dz:-bad",          # name can't start with '-'
]


@pytest.mark.parametrize("text", MALFORMED)
def test_malformed_rejected(text):
    with pytest.raises(FQCNParseError):
        parse(text)
    assert is_bangpath(text) is False


def test_is_bangpath_true():
    assert is_bangpath("dz:.kit.channels.verbosity") is True


def test_parsedpath_is_hashable():
    # NamedTuple + tuple segments -> usable as a dict key / set member.
    p = parse("dz:.kit")
    assert isinstance(p, ParsedPath)
    {p: 1}


# ---------------------------------------------------------------------------
# 3a1" -- the grammar core amendments (v2 contract Revision 1)
# ---------------------------------------------------------------------------

from dazzlecmd_lib.fqcn_grammar import (  # noqa: E402
    PLANE_ENTITY,
    PLANE_FIBER,
    PLANE_PROPERTY,
    PLANE_SUBKEY,
    PLANE_SUPRA,
    is_operator_led,
    parse_cli,
    segment_planes,
)


class TestImplicitRoot:
    """R1.2: the implicit root is SELF (engine-derived), never hardcoded."""

    def test_prop_led_equals_rooted(self):
        assert parse(".note", implicit_root="dz") == parse("dz.note")

    def test_fiber_led_equals_rooted(self):
        assert parse(
            ":.kit.channels.verbosity", implicit_root="dz"
        ) == parse("dz:.kit.channels.verbosity")

    def test_root_generic_not_dz(self):
        # AC-1 root-generic: wtf's engine passes its OWN root.
        assert parse(".note", implicit_root="wtf") == parse("wtf.note")

    def test_rooted_path_unaffected(self):
        assert parse("dz:.kit", implicit_root="wtf") == parse("dz:.kit")

    def test_operator_led_without_implicit_root_errors(self):
        with pytest.raises(FQCNParseError):
            parse(".note")


class TestPropertyPlaneModeSwitch:
    """R1.5: case-preserving sub-keys; no fiber/supra inside properties."""

    def test_env_vars_debug_parses(self):
        # SD-FQCN-1's flagship vector, previously rejected on case.
        parsed = parse("dz.env-vars:DEBUG")
        assert parsed.segments == (
            Segment(OP_PROP, "env-vars"),
            Segment(OP_PATH, "DEBUG"),
        )

    def test_subkey_case_distinct(self):
        # Windows env footgun: DEBUG and debug are DISTINCT sub-keys.
        assert parse("dz.env-vars:DEBUG") != parse("dz.env-vars:debug")

    def test_subkey_round_trips_case(self):
        assert unparse(parse("dz.env-vars:DEBUG")) == "dz.env-vars:DEBUG"

    def test_tree_segments_stay_lowercase(self):
        with pytest.raises(FQCNParseError):
            parse("dz:.KIT")           # fiber plane: lowercase only
        with pytest.raises(FQCNParseError):
            parse("dz.NOTE")           # property NAMES are ours: lowercase

    def test_interior_fiber_in_property_plane_errors(self):
        with pytest.raises(FQCNParseError):
            parse("dz.note:.weird")

    def test_interior_supra_in_property_plane_errors(self):
        with pytest.raises(FQCNParseError):
            parse("dz.note:+x")

    def test_subkey_then_nested_property(self):
        # '.' after a sub-key stays a lowercase property step.
        parsed = parse("dz.env-vars:DEBUG.note")
        assert parsed.segments[-1] == Segment(OP_PROP, "note")


class TestParseCli:
    """R1.6: the trailing ':.' listing production (V-L1/2/3)."""

    def test_v_l1_bare_listing(self):
        parsed, trailing = parse_cli(":.", implicit_root="dz")
        assert parsed == ParsedPath("dz", ())
        assert trailing == ":."

    def test_v_l2_node_listing(self):
        parsed, trailing = parse_cli("dz:.kit:.")
        assert parsed == parse("dz:.kit")
        assert trailing == ":."

    def test_property_subkey_listing_order(self):
        # C-5 order vector: the trailing split runs BEFORE the
        # interior-op check, so '.env-vars:.' is a listing, not an error.
        parsed, trailing = parse_cli(".env-vars:.", implicit_root="dz")
        assert parsed == parse("dz.env-vars")
        assert trailing == ":."

    def test_v_l3_segmentless_mid_path_errors(self):
        with pytest.raises(FQCNParseError):
            parse_cli("dz:.:.")

    def test_reserved_trailing_ops_error(self):
        for text in ("dz.note.", "dz:kit:", "."):
            with pytest.raises(FQCNParseError):
                parse_cli(text, implicit_root="dz")

    def test_no_trailing_passthrough(self):
        parsed, trailing = parse_cli("dz:.kit.channels.verbosity")
        assert trailing is None
        assert parsed == parse("dz:.kit.channels.verbosity")

    def test_bare_listing_needs_root(self):
        with pytest.raises(FQCNParseError):
            parse_cli(":.")


class TestOperatorLedAndPlanes:
    def test_is_operator_led(self):
        for text in (".note", ":.kit", ":+kit", ":grep"):
            assert is_operator_led(text) is True
        for text in ("grep", "dz:.kit", "-v", "--json", "", None):
            assert is_operator_led(text) is False

    def test_segment_planes(self):
        parsed = parse("dz:grep.env-vars:DEBUG")
        assert segment_planes(parsed) == (
            PLANE_ENTITY, PLANE_PROPERTY, PLANE_SUBKEY,
        )

    def test_segment_planes_fiber_supra(self):
        parsed = parse("dz:+kit:.verb")
        assert segment_planes(parsed) == (PLANE_SUPRA, PLANE_FIBER)


def test_bare_supra_is_now_legal():
    # CONTRACT CHANGE (C2+, 2026-07-08): bare :+ / :++ = parent /
    # grandparent -- ascent is deterministic so no key is required
    # (RS-4). Previously rejected as malformed.
    from dazzlecmd_lib.fqcn_grammar import canonicalize
    assert canonicalize("dz:+")[0] == "dz:+"
    assert canonicalize("dz:kit:++")[0] == "dz:kit:++"


class TestOperatorNotNameDot:
    """THE ONE-MEANING PIN (user consistency probe 2026-07-08): `:.` is
    the RING/FIBER OPERATOR; parsed names are BARE (the dot is never
    part of a name -- it only appears in serialized key strings). The
    hidden-name reading (B-10) is an address-algebra equivalence, not a
    second mechanism -- and these vectors keep the two readings from
    ever diverging."""

    def test_parsed_model_is_operator_plus_bare_name(self):
        from dazzlecmd_lib.fqcn_grammar import parse
        p = parse("dz:.meta:verb")
        assert [(s.op, s.name) for s in p.segments] == [
            (":.", "meta"), (":", "verb")]  # BARE 'meta', never '.meta'

    def test_dot_runs_rejected_everywhere(self):
        from dazzlecmd_lib.fqcn_grammar import canonicalize, FQCNParseError
        for bad in ("dz:..meta", "dz:.meta:..x", "dz:...x"):
            with pytest.raises(FQCNParseError):
                canonicalize(bad)

    def test_plain_step_cannot_forge_a_dot_led_name(self):
        # a dot-led segment can ONLY arise as the operator's
        # serialization -- ':' followed by '.' always tokenizes as the
        # fiber operator, never as a name starting with '.'
        from dazzlecmd_lib.fqcn_grammar import parse
        p = parse("dz:.alias")
        assert p.segments[0].op == ":." and p.segments[0].name == "alias"
