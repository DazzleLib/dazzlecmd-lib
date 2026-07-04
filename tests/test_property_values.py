"""Tests for 3a2" -- path canonicalization (fqcn_grammar.canonicalize +
FIBER_ROOTS), value-token parsing (property_values), and the
boundary-aware store family matcher (v2 contract R1.3/R1.4/R1.6).
"""

from __future__ import annotations

import argparse

import pytest

from dazzlecmd_lib.fqcn_grammar import (
    FIBER_ROOTS,
    FQCNParseError,
    canonicalize,
)
from dazzlecmd_lib.property_store import PropertyStore, key_in_family
from dazzlecmd_lib.property_values import (
    is_negative_number_token,
    parse_property_value,
)


class TestCanonicalize:
    """R1.3: the first-segment FIBER_ROOTS rule."""

    def test_fiber_vocabulary_passes_verbatim(self):
        text, forgiven = canonicalize(":.kit.channels.verbosity", implicit_root="dz")
        assert text == "dz:.kit.channels.verbosity"
        assert forgiven is False

    def test_root_property_forgiven(self):
        # AC-11: the user's muscle-memory spelling.
        text, forgiven = canonicalize(":.note", implicit_root="dz")
        assert text == "dz.note"
        assert forgiven is True

    def test_compound_property_path_forgiven_whole(self):
        # The case that killed single-segment-only forgiveness (Rnd2 A4).
        text, forgiven = canonicalize(":.note.author", implicit_root="dz")
        assert text == "dz.note.author"
        assert forgiven is True

    def test_subkey_preserved_through_forgiveness(self):
        text, forgiven = canonicalize(":.env-vars:DEBUG", implicit_root="dz")
        assert text == "dz.env-vars:DEBUG"  # case + sub-key preserved
        assert forgiven is True

    def test_interior_fiber_after_forgiven_errors(self):
        # A4 refinement: don't guess -- ':.note:.weird' is an error.
        with pytest.raises(FQCNParseError):
            canonicalize(":.note:.weird", implicit_root="dz")

    def test_level_excluded_from_fiber_roots(self):
        # C-3: level is the root PROPERTY; ':.level' forgives to '.level'.
        assert "level" not in FIBER_ROOTS
        text, forgiven = canonicalize(":.level", implicit_root="dz")
        assert text == "dz.level"
        assert forgiven is True

    def test_property_led_needs_no_forgiveness(self):
        text, forgiven = canonicalize(".note", implicit_root="dz")
        assert text == "dz.note"
        assert forgiven is False

    def test_entity_paths_untouched(self):
        text, forgiven = canonicalize("dz:grep.note")
        assert text == "dz:grep.note"
        assert forgiven is False

    def test_root_generic(self):
        text, forgiven = canonicalize(":.note", implicit_root="wtf")
        assert text == "wtf.note"
        assert forgiven is True


class TestNegativeNumberToken:
    """R1.4: pinned to argparse's own matcher -- verified against the
    live ArgumentParser so CPython drift surfaces as a test failure."""

    ACCEPT = ["-3", "-0.5", "-.5", "-42", "-0.0"]
    REJECT = ["-1e5", "-3.", "-inf", "--", "-v", "-", "3", "0.5", ""]

    @pytest.mark.parametrize("tok", ACCEPT)
    def test_accepts(self, tok):
        assert is_negative_number_token(tok) is True

    @pytest.mark.parametrize("tok", REJECT)
    def test_rejects(self, tok):
        assert is_negative_number_token(tok) is False

    def test_matches_live_argparse(self):
        # The provenance check: our pattern == argparse's actual matcher.
        p = argparse.ArgumentParser()
        pattern = p._negative_number_matcher.pattern
        for tok in self.ACCEPT:
            assert p._negative_number_matcher.match(tok), (
                f"argparse ({pattern!r}) rejects {tok!r} but we accept -- "
                f"asymmetry reborn; re-pin the pattern"
            )
        for tok in ["-1e5", "-3.", "-inf"]:
            assert not p._negative_number_matcher.match(tok)


class TestParsePropertyValue:
    def test_counted_verbosity(self):
        assert parse_property_value("vvvv") == 4
        assert parse_property_value("qqq") == -3
        assert parse_property_value("v") == 1

    def test_numbers(self):
        assert parse_property_value("-3") == -3
        assert parse_property_value("4") == 4
        assert parse_property_value("-0.5") == -0.5

    def test_named_ranks(self):
        ranks = {"errors": -3, "full-debug": 5}
        assert parse_property_value("errors", named_ranks=ranks) == -3
        assert parse_property_value("full-debug", named_ranks=ranks) == 5

    def test_json_literals(self):
        assert parse_property_value("true") is True
        assert parse_property_value("null") is None
        assert parse_property_value('["a","b"]') == ["a", "b"]

    def test_plain_string_falls_through(self):
        assert parse_property_value("hello world") == "hello world"

    def test_empty_token_errors_with_delete_hint(self):
        # R1.4: PS 5.1 drops "" -- empty can never be load-bearing.
        with pytest.raises(ValueError, match="prop delete"):
            parse_property_value("")

    def test_named_rank_beats_json(self):
        # a rank named like a JSON literal resolves as the rank
        assert parse_property_value("true", named_ranks={"true": 9}) == 9


class TestBoundaryAwareFamily:
    """R1.6 / C-2: the kitchen probe, verbatim from the collabN review."""

    def test_key_in_family(self):
        assert key_in_family("dz:.kit", "dz:.kit") is True
        assert key_in_family("dz:.kit.channels.verbosity", "dz:.kit") is True
        assert key_in_family("dz:.kit:sub", "dz:.kit") is True
        assert key_in_family("dz:.kitchen.note", "dz:.kit") is False

    def test_kitchen_probe(self, tmp_path):
        store = PropertyStore(config_dir=str(tmp_path))
        store.set("dz:.kit.channels.verbosity", 3)
        store.set("dz:.kitchen.note", "oops")
        store.set("dz:.kit", "node-value")
        fam = store.list_prefix("dz:.kit")
        assert fam == {
            "dz:.kit.channels.verbosity": 3,
            "dz:.kit": "node-value",
        }
        assert "dz:.kitchen.note" not in fam


class TestAxisRungException:
    """2026-07-04 field find: ':.level:kit' is the RUNG NODE (verbatim),
    while ':.level' alone still forgives to the property -- the axis's
    rung vocabulary is the discriminator syntax cannot provide."""

    def test_rung_path_stays_verbatim(self):
        text, forgiven = canonicalize(":.level:kit", implicit_root="dz")
        assert text == "dz:.level:kit"
        assert forgiven is False

    def test_rung_with_property_chain_verbatim(self):
        text, forgiven = canonicalize(
            ":.level:kit.channels.verbosity", implicit_root="dz")
        assert text == "dz:.level:kit.channels.verbosity"
        assert forgiven is False

    def test_bare_level_still_forgives_to_the_property(self):
        text, forgiven = canonicalize(":.level", implicit_root="dz")
        assert text == "dz.level"
        assert forgiven is True

    def test_non_rung_continuation_still_forgives(self):
        # ':.level:bogus' -- 'bogus' is not a rung; property sub-key wins
        text, forgiven = canonicalize(":.level:bogus", implicit_root="dz")
        assert text == "dz.level:bogus"
        assert forgiven is True

    def test_env_vars_subkey_unaffected(self):
        text, forgiven = canonicalize(":.env-vars:DEBUG", implicit_root="dz")
        assert text == "dz.env-vars:DEBUG"
        assert forgiven is True
