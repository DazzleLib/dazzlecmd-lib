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
    "dz:+",             # trailing supra
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
