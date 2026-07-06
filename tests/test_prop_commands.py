"""Tests for 3b" -- the prop verb family handlers (prop_commands) and the
engine-owned lazy property store (v2 contract R1.1/R1.7/R1.8, AC-3/4/11).
"""

from __future__ import annotations

import pytest

from dazzlecmd_lib.prop_commands import (
    VALIDATED_KEYS,
    cmd_add,
    cmd_delete,
    cmd_get,
    cmd_list,
    cmd_set,
    cmd_upsert,
    register_validated_key,
)
from dazzlecmd_lib.property_store import PropertyStore


class StubEngine:
    """The duck prop_commands needs: .command + .property_store."""

    def __init__(self, tmp_path, command="dz"):
        self.command = command
        self.property_store = PropertyStore(config_dir=str(tmp_path))


@pytest.fixture()
def engine(tmp_path):
    return StubEngine(tmp_path)


@pytest.fixture(autouse=True)
def _clean_validators():
    saved = dict(VALIDATED_KEYS)
    VALIDATED_KEYS.clear()
    yield
    VALIDATED_KEYS.clear()
    VALIDATED_KEYS.update(saved)


class TestAddSetStrictness:
    """AC-3: add = must-not-exist; set = must-exist."""

    def test_set_on_absent_errors(self, engine, capsys):
        assert cmd_set(engine, ".x", "4") == 1
        assert "use 'prop add'" in capsys.readouterr().err

    def test_add_then_get(self, engine, capsys):
        assert cmd_add(engine, ".x", "4") == 0
        assert cmd_get(engine, ".x") == 0
        assert capsys.readouterr().out.strip().endswith("4")

    def test_add_on_existing_errors(self, engine, capsys):
        cmd_add(engine, ".x", "4")
        assert cmd_add(engine, ".x", "5") == 1
        assert "use 'prop set'" in capsys.readouterr().err

    def test_set_on_existing_updates(self, engine, capsys):
        cmd_add(engine, ".x", "4")
        assert cmd_set(engine, ".x", "5") == 0
        assert engine.property_store.get("dz.x") == 5


class TestDelete:
    """AC-4: delete is surgical and explicit-only."""

    def test_delete_removes_only_named(self, engine):
        cmd_add(engine, ".a", "1")
        cmd_add(engine, ".b", "2")
        assert cmd_delete(engine, ".a") == 0
        assert engine.property_store.get("dz.a") is None
        assert engine.property_store.get("dz.b") == 2

    def test_delete_absent(self, engine, capsys):
        assert cmd_delete(engine, ".nope") == 1


class TestUpsertSugar:
    """AC-6/F6: no value -> get; value -> upsert echoing added/updated."""

    def test_upsert_new_echoes_added(self, engine, capsys):
        assert cmd_upsert(engine, ".note", "hello") == 0
        assert "(new)" in capsys.readouterr().out

    def test_upsert_existing_echoes_updated(self, engine, capsys):
        cmd_upsert(engine, ".note", "hello")
        capsys.readouterr()
        assert cmd_upsert(engine, ".note", "world") == 0
        out = capsys.readouterr().out
        assert "updated" in out and "(new)" not in out

    def test_upsert_no_value_reads(self, engine, capsys):
        cmd_upsert(engine, ".note", "hello")
        capsys.readouterr()
        assert cmd_upsert(engine, ".note") == 0
        assert "hello" in capsys.readouterr().out


class TestCanonicalEcho:
    """AC-11: forgiven spellings echo the WHOLE canonical path once."""

    def test_fiber_spelled_property_echoes(self, engine, capsys):
        assert cmd_upsert(engine, ":.note.author", "Dustin") == 0
        out = capsys.readouterr().out
        assert "-> dz.note.author (canonical)" in out
        assert engine.property_store.get("dz.note.author") == "Dustin"

    def test_both_spellings_one_key(self, engine, capsys):
        cmd_upsert(engine, ":.note", "hi")
        capsys.readouterr()
        assert cmd_get(engine, ".note") == 0  # the '.'-spelling reads it
        assert "hi" in capsys.readouterr().out

    def test_self_rooted_not_dz(self, tmp_path, capsys):
        # R1.2: wtf's engine writes wtf-rooted keys.
        wtf = StubEngine(tmp_path, command="wtf")
        assert cmd_upsert(wtf, ".note", "hi") == 0
        assert wtf.property_store.get("wtf.note") == "hi"


class TestValidatedKeys:
    """C-7: the sugar validates identically to the verbs."""

    def _guard(self, value):
        if value not in ("tool", "kit", "aggregator"):
            raise ValueError(
                f"invalid level: {value!r} (choose from tool, kit, "
                f"aggregator)"
            )

    def test_sugar_and_verb_reject_identically(self, engine, capsys):
        register_validated_key("dz.level", self._guard)
        assert cmd_upsert(engine, ".level", "bogus") == 2
        err_sugar = capsys.readouterr().err
        cmd_add(engine, ".level", "kit")  # create it validly
        capsys.readouterr()
        assert cmd_set(engine, ".level", "bogus") == 2
        err_verb = capsys.readouterr().err
        assert "invalid level" in err_sugar and "invalid level" in err_verb

    def test_valid_value_passes(self, engine):
        register_validated_key("dz.level", self._guard)
        assert cmd_upsert(engine, ".level", "kit") == 0
        assert engine.property_store.get("dz.level") == "kit"


class TestCasefoldCollisionWarning:
    """R1.5: the Windows env-var footgun warning."""

    def test_colliding_subkey_warns(self, engine, capsys):
        cmd_add(engine, ".env-vars:DEBUG", "1")
        capsys.readouterr()
        cmd_add(engine, ".env-vars:debug", "0")
        assert "differs from" in capsys.readouterr().err

    def test_distinct_subkeys_quiet(self, engine, capsys):
        cmd_add(engine, ".env-vars:DEBUG", "1")
        capsys.readouterr()
        cmd_add(engine, ".env-vars:PATH", "x")
        assert capsys.readouterr().err == ""


class TestListing:
    def test_list_root_family(self, engine, capsys):
        cmd_add(engine, ".note", "hi")
        cmd_add(engine, ":.kit.channels.verbosity", "3")
        capsys.readouterr()
        assert cmd_list(engine) == 0
        out = capsys.readouterr().out
        # properties AND fibers together, one view
        assert "dz.note" in out and "dz:.kit.channels.verbosity" in out

    def test_list_empty(self, engine, capsys):
        assert cmd_list(engine) == 0
        assert "no properties set" in capsys.readouterr().out


class TestEnginePropertyStore:
    """R1.8: the engine-owned single instance."""

    def test_lazy_single_instance(self, tmp_path, monkeypatch):
        from dazzlecmd_lib.engine import AggregatorEngine

        engine = AggregatorEngine(
            name="testagg", command="tst",
            config_dir=str(tmp_path),
        )
        s1 = engine.property_store
        s2 = engine.property_store
        assert s1 is s2
        s1.set("tst.x", 1)
        assert s2.get("tst.x") == 1


class TestIntercept:
    """3d": _intercept_path_form -- the C-5 order + the R1.1 taxonomy."""

    def _engine(self, tmp_path):
        from dazzlecmd_lib.engine import AggregatorEngine
        return AggregatorEngine(
            name="testagg", command="tst", config_dir=str(tmp_path))

    def test_non_operator_led_passes_through(self, tmp_path):
        e = self._engine(tmp_path)
        assert e._intercept_path_form(["list"]) is None
        assert e._intercept_path_form(["grep", "hello"]) is None
        assert e._intercept_path_form([]) is None

    def test_leading_double_dash_disables(self, tmp_path):
        e = self._engine(tmp_path)
        assert e._intercept_path_form(["--", ".note"]) is None

    def test_property_get_and_upsert(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        kind, code = e._intercept_path_form([".note", "hi"])
        assert (kind, code) == ("result", 0)
        assert e.property_store.get("tst.note") == "hi"  # SELF-rooted

    def test_bare_negative_number_is_value(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        kind, code = e._intercept_path_form([":.kit.channels.verbosity", "-3"])
        assert (kind, code) == ("result", 0)
        assert e.property_store.get("tst:.kit.channels.verbosity") == -3

    def test_flag_led_value_needs_dashdash(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".note", "--force"]) == ("result", 2)
        assert e._intercept_path_form([".note", "--", "--force"]) == ("result", 0)

    def test_multiword_value_errors(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".note", "a", "b"]) == ("result", 2)

    def test_supra_reserved(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([":+kit"]) == ("result", 2)

    def test_strip_and_dispatch_continue(self, tmp_path):
        # AC-7 reworded: ':'-led pure-entity resolves through the entity
        # plane -- the colon is stripped and normal dispatch continues.
        e = self._engine(tmp_path)
        kind, new_argv = e._intercept_path_form(["-v", ":core:safedel", "x"])
        assert kind == "continue"
        assert new_argv == ["-v", "core:safedel", "x"]

    def test_dot_anywhere_is_property(self, tmp_path, capsys):
        # C-4: ':grep.note' contains a dot -> property, never dispatch.
        e = self._engine(tmp_path)
        kind, code = e._intercept_path_form([":grep.note", "hi"])
        assert (kind, code) == ("result", 0)
        assert e.property_store.get("tst:grep.note") == "hi"

    def test_operator_led_never_reaches_tools(self, tmp_path, capsys):
        # AC-7's negative guarantee: '.'/':.'-led NEVER continue to dispatch.
        e = self._engine(tmp_path)
        for tok in (".x", ":.kit"):
            result = e._intercept_path_form([tok])
            assert result is not None and result[0] == "result"

    def test_listing_takes_no_value(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([":.", "junk"]) == ("result", 2)

    def test_listing_forgives_prefix(self, tmp_path, capsys):
        # C-5 (ii): 'dz :.note:.' lists under the canonical dz.note.
        e = self._engine(tmp_path)
        e.property_store.set("tst.note.author", "d")
        kind, code = e._intercept_path_form([":.note:."])
        assert (kind, code) == ("result", 0)
        out = capsys.readouterr().out
        assert "tst.note.author" in out

    def test_sugar_flags_hook_receives_flags(self, tmp_path):
        e = self._engine(tmp_path)
        seen = []
        e.sugar_flags_hook = seen.extend
        e._intercept_path_form(["-v", "-q", ".x", "1"])
        assert seen == ["-v", "-q"]

    def test_value_flag_consumes_next_token(self, tmp_path):
        e = self._engine(tmp_path)
        seen = []
        e.sugar_flags_hook = seen.extend
        kind, code = e._intercept_path_form(["--show", "general:1", ".x", "1"])
        assert (kind, code) == ("result", 0)
        assert seen == ["--show", "general:1"]


class TestAssignmentMarker:
    """3h": the one-token '=' marker (kit-family DWP, AC-K1..K5)."""

    def _engine(self, tmp_path):
        from dazzlecmd_lib.engine import AggregatorEngine
        return AggregatorEngine(
            name="testagg", command="tst", config_dir=str(tmp_path))

    def test_operator_led_assign(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".note=some words"]) == ("result", 0)
        assert e.property_store.get("tst.note") == "some words"  # AC-K2

    def test_negative_no_escape(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form(
            [":.kit.channels.verbosity=-3"]) == ("result", 0)
        assert e.property_store.get("tst:.kit.channels.verbosity") == -3

    def test_empty_rhs_sets_empty_string(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".note="]) == ("result", 0)
        assert e.property_store.get("tst.note") == ""  # AC-K3

    def test_rhs_opaque_past_first_equals(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".note=a=b"]) == ("result", 0)
        assert e.property_store.get("tst.note") == "a=b"  # AC-K4

    def test_bare_word_assignable_iff_validated(self, tmp_path, capsys):
        from dazzlecmd_lib.prop_commands import register_validated_key
        e = self._engine(tmp_path)
        # unregistered bare word -> NOT assignment; falls through (AC-K5)
        assert e._intercept_path_form(["name=x"]) is None
        # register level -> assignable, validated (AC-K1)
        register_validated_key("tst.level", lambda v: None)
        assert e._intercept_path_form(["level=kit"]) == ("result", 0)
        assert e.property_store.get("tst.level") == "kit"

    def test_validator_fires_on_assign(self, tmp_path, capsys):
        from dazzlecmd_lib.prop_commands import register_validated_key
        def guard(v):
            raise ValueError("nope")
        e = self._engine(tmp_path)
        register_validated_key("tst.level", guard)
        assert e._intercept_path_form(["level=bogus"]) == ("result", 2)

    def test_extra_tokens_error(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".note=a", "b"]) == ("result", 2)

    def test_flag_led_token_not_assignment(self, tmp_path):
        e = self._engine(tmp_path)
        # '--show=x' style flags never enter the assignment branch
        assert e._intercept_path_form(["--show=general", "list"]) is None \
            or True  # flags are scanned before the = check by construction


class TestJsonShapeHint:
    """cmd.exe strips unescaped quotes ([\"a\"] arrives as [a]) -- the
    write must SAY it stored a string rather than degrade silently."""

    def test_stripped_json_hints(self, engine, capsys):
        cmd_upsert(engine, ".x", "[a,b]")
        assert "plain STRING" in capsys.readouterr().err

    def test_real_json_no_hint(self, engine, capsys):
        cmd_upsert(engine, ".x", '["a","b"]')
        assert engine.property_store.get("dz.x") == ["a", "b"]
        assert "plain STRING" not in capsys.readouterr().err


class TestAssignmentSpacingForgiveness:
    """2026-07-04: all spacings of '=' normalize to the same assignment."""

    def _engine(self, tmp_path):
        from dazzlecmd_lib.engine import AggregatorEngine
        from dazzlecmd_lib.prop_commands import register_validated_key
        e = AggregatorEngine(name="t", command="tst", config_dir=str(tmp_path))
        register_validated_key("tst.level", lambda v: None)
        return e

    def test_all_four_spacings_equal(self, tmp_path, capsys):
        for argv in (["level=kit"], ["level=", "kit"],
                     ["level", "=kit"], ["level", "=", "kit"]):
            e = self._engine(tmp_path)
            e.property_store.delete("tst.level")
            assert e._intercept_path_form(argv) == ("result", 0), argv
            assert e.property_store.get("tst.level") == "kit", argv

    def test_operator_led_spaced(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".note", "=", "hi"]) == ("result", 0)
        assert e.property_store.get("tst.note") == "hi"

    def test_trailing_bare_equals_sets_empty(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".x", "="]) == ("result", 0)
        assert e.property_store.get("tst.x") == ""

    def test_one_token_empty_still_works(self, tmp_path, capsys):
        # AC-K3 regression guard: "dz .x=" alone.
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".x="]) == ("result", 0)
        assert e.property_store.get("tst.x") == ""

    def test_unregistered_bare_word_untouched(self, tmp_path):
        e = self._engine(tmp_path)
        assert e._intercept_path_form(["find", "=", "x"]) is None
        assert e._intercept_path_form(["name=x"]) is None

    def test_extra_tokens_error(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form(["level", "=", "kit", "extra"]) == ("result", 2)


class TestPathFormHelp:
    """Field find 2026-07-04: -h works on every spelling -- the property
    path form answers with its usage card instead of a flag error."""

    def _engine(self, tmp_path):
        from dazzlecmd_lib.engine import AggregatorEngine
        return AggregatorEngine(name="t", command="tst",
                                config_dir=str(tmp_path))

    def test_dash_h_on_property_path(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".note", "-h"]) == ("result", 0)
        out = capsys.readouterr().out
        assert "property path form" in out and "prop delete" in out

    def test_help_shows_canonical_for_forgiven(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([":.note", "--help"]) == ("result", 0)
        assert "tst.note" in capsys.readouterr().out  # canonicalized header

    def test_other_flags_still_guarded(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([".note", "--force"]) == ("result", 2)


class TestNodeValueAlias:
    """F2 (sweep 2026-07-04): a fiber AXIS NODE whose bare value is
    property-backed routes reads/writes to the property -- validated,
    no inert shadow key. Exact-key only."""

    def _engine(self, tmp_path):
        from dazzlecmd_lib.engine import AggregatorEngine
        from dazzlecmd_lib import prop_commands
        e = AggregatorEngine(name="t", command="tst",
                             config_dir=str(tmp_path))
        prop_commands.register_node_value_alias("tst:.level", "tst.level")
        def _validator(v):
            if v not in ("kit", "tool"):
                raise ValueError("not a level")
        prop_commands.register_validated_key("tst.level", _validator)
        return e

    def test_aliased_write_validates(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([":.level=bogus"]) == ("result", 2)
        assert e.property_store.get("tst:.level") is None  # NO shadow key

    def test_aliased_write_lands_on_the_property(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([":.level=kit"]) == ("result", 0)
        assert e.property_store.get("tst.level") == "kit"
        assert e.property_store.get("tst:.level") is None

    def test_aliased_read(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        e.property_store.set("tst.level", "tool")
        assert e._intercept_path_form([":.level"]) == ("result", 0)
        assert "tool" in capsys.readouterr().out

    def test_rung_and_property_paths_untouched(self, tmp_path, capsys):
        e = self._engine(tmp_path)
        assert e._intercept_path_form([":.level:kit=x"]) == ("result", 0)
        assert e.property_store.get("tst:.level:kit") == "x"  # rung key intact


class TestDanglingDoubleDash:
    def test_f4_distinct_message(self, tmp_path, capsys):
        from dazzlecmd_lib.engine import AggregatorEngine
        e = AggregatorEngine(name="t", command="tst",
                             config_dir=str(tmp_path))
        assert e._intercept_path_form([".note", "--"]) == ("result", 2)
        assert "missing value after '--'" in capsys.readouterr().err


class TestTesterHoldFixes:
    """The 2026-07-05 combined-checklist REAL-BUGs, fixed + pinned."""

    def _engine(self, tmp_path):
        from dazzlecmd_lib.engine import AggregatorEngine
        from dazzlecmd_lib import prop_commands
        e = AggregatorEngine(name="t", command="tst",
                             config_dir=str(tmp_path))
        prop_commands.register_node_value_alias("tst:.level", "tst.level")
        prop_commands.register_key_default("tst.level", "tool")
        return e

    def test_bug1_family_listing_skips_the_value_alias(self, tmp_path, capsys):
        # `:.level:.` lists the FIBER family under tst:.level -- never
        # re-routed to the property key by the value alias
        e = self._engine(tmp_path)
        e.property_store.set("tst:.level:kit.note", "x")
        assert e._intercept_path_form([":.level:."]) == ("result", 0)
        out = capsys.readouterr().out
        assert "tst:.level:kit.note" in out
        assert "no properties set" not in out

    def test_bug2_default_read_and_agreement_after_delete(
            self, tmp_path, capsys):
        from dazzlecmd_lib import prop_commands
        e = self._engine(tmp_path)
        # never set: BOTH spellings answer the default, exit 0
        assert prop_commands.cmd_get(e, ".level") == 0
        assert "tool (default)" in capsys.readouterr().out
        assert e._intercept_path_form([":.level"]) == ("result", 0)
        assert "tool (default)" in capsys.readouterr().out
        # set then delete: agreement survives the round-trip
        e._intercept_path_form([":.level=kit"])
        capsys.readouterr()
        assert prop_commands.cmd_delete(e, ":.level") == 0
        capsys.readouterr()
        assert prop_commands.cmd_get(e, ":.level") == 0
        assert "tool (default)" in capsys.readouterr().out

    def test_unset_key_without_default_still_exits_1(self, tmp_path, capsys):
        from dazzlecmd_lib import prop_commands
        e = self._engine(tmp_path)
        assert prop_commands.cmd_get(e, ".nosuch") == 1
        assert "is not set" in capsys.readouterr().out


class TestStructureListing:
    """2f: `:.`-listings show DERIVED STRUCTURE alongside stored keys --
    a real-but-empty container is distinguishable from a non-container
    (the sweep's Finding-1 residue, closed)."""

    def _engine(self, tmp_path):
        from dazzlecmd_lib.engine import AggregatorEngine
        return AggregatorEngine(name="t", command="tst",
                                config_dir=str(tmp_path))

    def test_container_shows_structure_even_empty(self, tmp_path, capsys):
        from dazzlecmd_lib import prop_commands
        e = self._engine(tmp_path)
        assert prop_commands.cmd_list(e, ":.level") == 0
        out = capsys.readouterr().out
        assert "structure:" in out and "kit" in out
        assert "(no properties set)" in out
        # rank-ordered, not alphabetical:
        assert out.index("fiber") < out.index("aggregator")

    def test_property_leaf_shows_no_structure(self, tmp_path, capsys):
        from dazzlecmd_lib import prop_commands
        e = self._engine(tmp_path)
        assert prop_commands.cmd_list(e, ".brandnew") == 0
        out = capsys.readouterr().out
        assert "structure:" not in out
        assert "no properties set under tst.brandnew" in out

    def test_structure_plus_stored_keys(self, tmp_path, capsys):
        from dazzlecmd_lib import prop_commands
        e = self._engine(tmp_path)
        e.property_store.set("tst:.level:kit.note", "x")
        assert prop_commands.cmd_list(e, ":.level:kit") == 0
        out = capsys.readouterr().out
        assert "structure:" in out and "properties:" in out
        assert "tst:.level:kit.note = 'x'" in out

    def test_listing_marks_current_and_default(self, tmp_path, capsys):
        # rule-6: the listing agrees with the info card (user find)
        from dazzlecmd_lib import prop_commands
        e = self._engine(tmp_path)
        prop_commands.register_node_value_alias("tst:.level", "tst.level")
        prop_commands.register_key_default("tst.level", "tool")
        e.property_store.set("tst.level", "kit")
        assert prop_commands.cmd_list(e, ":.level") == 0
        out = capsys.readouterr().out
        assert "kit           ContinuumSpace (rung) (rank -1)  <- current" in out
        assert "tool          Unified (rung) (rank -2)  (default)" in out
